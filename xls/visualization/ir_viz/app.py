# Lint as: python3
# Copyright 2020 The XLS Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""A tool to visualize XLS IR.

Exposes a text box to edit or cut-and-paste XLS IR to render as a graph.
"""

import json
import os
import sys

from typing import List, Tuple

from absl import app
from absl import flags

import flask

from xls.common import runfiles
from xls.common.python import init_xls
from xls.visualization.ir_viz.python import ir_to_json

FLAGS = flags.FLAGS
flags.DEFINE_bool('use_ipv6', False, 'Whether to use IPv6.')
flags.DEFINE_integer('port', None, 'Port to serve on.')
flags.DEFINE_string('delay_model', None, 'Delay model to use.')
# TODO(meheff): Remove this flag and figure out a better way getting the actual
# schedule used in the examples and/or surface the scheduling options in the
# UI.
flags.DEFINE_integer(
    'pipeline_stages', None,
    'Schedule the IR function into this many stages using the specified '
    'delay model. This influence the rendering graph by changing '
    'cycle-spanning edges to dotted style')
flags.DEFINE_string(
    'preload_ir_path', None, 'Path to local IR file to render on startup. If '
    '"-" then read from stdin.')
flags.DEFINE_string(
    'example_ir_dir', None, 'Path to directory containing IR files to use as '
    'precanned examples available in the UI via the "Examples" drop down menu. '
    'All files ending in ".ir" in the directory are used.')
flags.mark_flag_as_required('delay_model')

IR_EXAMPLES_FILE_LIST = 'xls/visualization/ir_viz/ir_examples_file_list.txt'

webapp = flask.Flask('XLS UI')
webapp.debug = True

# Set of pre-canned examples as a list of (name, IR text) tuples. By default
# these are loaded from IR_EXAMPLES_FILE_LIST unless --example_ir_dir is given.
examples = []


def load_precanned_examples() -> List[Tuple[str, str]]:
  """Returns a list of examples as tuples of (name, IR text).

  Examples are loaded from the list of files in IR_EXAMPLES_FILE_LIST.

  Returns:
    List of tuples containing (name, IR text).
  """
  irs = []
  for ir_path in runfiles.get_contents_as_text(IR_EXAMPLES_FILE_LIST).split():
    if '.opt.ir' not in ir_path:
      continue

    def strip_up_to(s, part):
      if part in s:
        return s[s.find(part) + len(part):]
      return s

    # Strip off path prefix up to 'examples/' or 'modules/' to create the
    # example name.
    if 'examples/' in ir_path:
      name = strip_up_to(ir_path, 'examples/')
    elif 'modules/' in ir_path:
      name = strip_up_to(ir_path, 'modules/')
    else:
      name = ir_path
    name = name[:-len('.opt.ir')]
    irs.append((name, runfiles.get_contents_as_text(ir_path)))
  irs.sort()
  return irs


def load_examples_from_dir(examples_dir):
  """Returns a list of examples as tuples of (name, IR text).

  Examples are loaded from the given directory and include all files ending with
  '.ir'.

  Args:
    examples_dir: Directory to read IR examples from.

  Returns:
    List of tuples containing (name, IR text).

  """
  irs = []
  for ir_filename in os.listdir(examples_dir):
    if not ir_filename.endswith('.ir'):
      continue

    with open(os.path.join(examples_dir, ir_filename)) as f:
      irs.append((ir_filename, f.read()))

  irs.sort()
  return irs


def get_third_party_js():
  """Returns the URLS of the third-party JS to load."""
  urls = runfiles.get_contents_as_text(
      'xls/visualization/ir_viz/third_party_js.txt').split()
  return [u.strip() for u in urls if u.strip()]


@webapp.route('/')
def splash():
  return flask.render_template_string(
      runfiles.get_contents_as_text(
          'xls/visualization/ir_viz/templates/splash.tmpl'),
      examples=[name for name, _ in examples],
      third_party_scripts=get_third_party_js(),
      load_default=FLAGS.preload_ir_path is not None)


@webapp.route('/static/<filename>')
def static_handler(filename):
  """Reads and returns static files.

  Args:
    filename: The name of the file. The file is loaded from the data deps under
      `xls/visualization/ir_viz`.

  Returns:
    Flask response.
  """

  try:
    content = runfiles.get_contents_as_text(
        flask.safe_join('xls/visualization/ir_viz', filename))
  except FileNotFoundError:
    flask.abort(404)
  if filename.endswith('.js'):
    ctype = 'application/javascript'
  elif filename.endswith('.css'):
    ctype = 'text/css'
  else:
    ctype = 'text/plain'
  return flask.Response(response=content, content_type=ctype)


@webapp.route('/examples/<path:filename>')
def example_handler(filename):
  """Reads and returns example IR files."""
  if filename == 'default' and FLAGS.preload_ir_path:
    if FLAGS.preload_ir_path == '-':
      contents = sys.stdin.read()
    else:
      with open(FLAGS.preload_ir_path) as f:
        contents = f.read()
    return flask.Response(response=contents, content_type='text/plain')
  try:
    for name, content in examples:
      if filename == name:
        return flask.Response(response=content, content_type='text/plain')
  except IOError:
    flask.abort(404)


@webapp.route('/graph', methods=['POST'])
def graph_handler():
  """Parses the posted text and returns a parse status."""
  text = flask.request.form['text']
  try:
    json_text = ir_to_json.ir_to_json(text, FLAGS.delay_model,
                                      FLAGS.pipeline_stages)
  except Exception as e:  # pylint: disable=broad-except
    # TODO(meheff): Switch to new pybind11 more-specific exception.
    return flask.jsonify({'error_code': 'error', 'message': str(e)})

  graph = json.loads(json_text)
  jsonified = flask.jsonify({'error_code': 'ok', 'graph': graph})
  return jsonified


def main(argv):
  if len(argv) > 1:
    raise app.UsageError('Too many command-line arguments.')

  # This is required so that module initializers are called including those
  # which register delay models.
  init_xls.init_xls(sys.argv)
  global examples
  if FLAGS.example_ir_dir is not None:
    examples = load_examples_from_dir(FLAGS.example_ir_dir)
  else:
    examples = load_precanned_examples()

  webapp.run(host='::' if FLAGS.use_ipv6 else '0.0.0.0', port=FLAGS.port)


if __name__ == '__main__':
  app.run(main)
