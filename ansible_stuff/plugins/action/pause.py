# ATTENTION !!!
# This is a modified version of a pause ActionModule: https://docs.ansible.com/ansible/latest/modules/pause_module.html
# It supports timeout for prompt: 'seconds' with default answer: 'timeout_answer'
# Minutes option was removed !
# Other functionality should work as previous.
#
# Copyright 2012, Tim Bielawa <tbielawa@redhat.com>
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import datetime
import signal
import sys
import termios
import time
import tty
from os import isatty
from ansible.errors import AnsibleError
from ansible.module_utils._text import to_text, to_native
from ansible.module_utils.parsing.convert_bool import boolean
from ansible.module_utils.six import PY3
from ansible.plugins.action import ActionBase
from ansible.utils.display import Display
from collections import defaultdict
from ansible.module_utils.basic import AnsibleModule


ANSIBLE_METADATA = {'metadata_version': '1.1',
                    'status': ['preview'],
                    'supported_by': 'community'}


DOCUMENTATION = '''
---
module: pause
short_description: Pause playbook execution
description:
  - Pauses playbook execution to get a prompt and continues with 'timeout_answer' if the option was set together with 'seconds' & 'prompt'
  - Pauses playbook execution for a set amount of time, or until a prompt is acknowledged
    All parameters are optional. The default behavior is to pause for a certain amount of time.
  - To pause/wait/sleep per host, use the M(wait_for) module.
  - You can use C(ctrl+c) if you wish to advance a pause earlier than it is set to expire or if you need to abort a playbook run entirely.
    To continue early press C(ctrl+c) and then C(c). To abort a playbook press C(ctrl+c) and then C(a).
  - The pause module integrates into async/parallelized playbooks without any special considerations (see Rolling Updates).
    When using pauses with the C(serial) playbook parameter (as in rolling updates) you are only prompted once for the current group of hosts.
  - This module wasn't tested on Windows targets!
version_added: "0.8"
options:
  seconds:
    description:
      - A positive number of seconds to pause for.
  prompt:
    description:
      - Optional, text to use for the prompt message.
  timeout_answer:
    description:
      - Optional, text to use for the default prompt message if TimeOut reached. It's usually used together with a 'seconds' option, which defines the TimeOut. 
  echo:
    description:
      - Controls whether or not keyboard input is shown when typing.
      - Has no effect if 'seconds' or 'minutes' is set.
    type: bool
    default: 'yes'
    version_added: 2.5

author: "Tim Bielawa (@tbielawa)"
notes:
      - Starting in 2.2,  if you specify 0 or negative for minutes or seconds, it will wait for 1 second, previously it would wait indefinitely.
      - This module wasn't tested on Windows targets!
'''

EXAMPLES = '''
# Pause until you provide some input, but continues with 'timeout_answer' if timeout in 'seconds' was reached.
- pause:
    prompt: 'Provide a version'
    echo: yes
    seconds: 60
    timeout_answer: '1.2.5'

# Pause for 5 minutes to build app cache.
- pause:
    seconds: 300

# Pause until you provide some input.
- pause:
    prompt: 'Provide a version'
    echo: yes
    
# Pause until you provide some input without printing it out, but continues with 'timeout_answer' if timeout in 'seconds' was reached. 
# May be useful if you want to input some sensitive data.
- pause:
    prompt: 'Provide a version'
    echo: no
    seconds: 60
    timeout_answer: '1.2.5'

# Pause to get some sensitive input.
- pause:
    prompt: "Enter a secret"
    echo: no
'''

RETURN = '''
user_input:
  description: User input from interactive console
  type: str
  sample: Example user input
start:
  description: Time when started pausing
  returned: always
  type: str
  sample: "2017-02-23 14:35:07.298862"
stop:
  description: Time when ended pausing
  returned: always
  type: str
  sample: "2017-02-23 14:35:09.552594"
delta:
  description: Time paused in seconds
  returned: always
  type: str
  sample: 2
stdout:
  description: Output of pause module
  returned: always
  type: str
  sample: Paused for 0.04 minutes
echo:
  description: Value of echo setting
  returned: always
  type: bool
  sample: true
'''


display = Display()

try:
    import curses

    # Nest the try except since curses.error is not available if curses did not import
    try:
        curses.setupterm()
        HAS_CURSES = True
    except curses.error:
        HAS_CURSES = False
except ImportError:
    HAS_CURSES = False

if HAS_CURSES:
    MOVE_TO_BOL = curses.tigetstr('cr')
    CLEAR_TO_EOL = curses.tigetstr('el')
else:
    MOVE_TO_BOL = b'\r'
    CLEAR_TO_EOL = b'\x1b[K'


class AnsibleTimeoutExceeded(Exception):
    pass


def timeout_handler(signum, frame):
    raise AnsibleTimeoutExceeded


def clear_line(stdout):
    stdout.write(b'\x1b[%s' % MOVE_TO_BOL)
    stdout.write(b'\x1b[%s' % CLEAR_TO_EOL)


class ActionModule(ActionBase):
    """ pauses execution until input is received or for a length or time """

    BYPASS_HOST_LOOP = True
    _VALID_ARGS = frozenset(('echo', 'prompt', 'seconds', 'timeout_answer'))

    def run(self, tmp=None, task_vars=None):
        """ run the pause action module """
        if task_vars is None:
            task_vars = dict()

        result = super(ActionModule, self).run(tmp, task_vars)
        del tmp  # tmp no longer has any effect

        duration_unit = 'seconds'
        prompt = None
        seconds = None
        echo = None
        echo_prompt = ''
        result.update(dict(
            changed=False,
            rc=0,
            stderr='',
            stdout='',
            start=None,
            stop=None,
            delta=None,
            echo=None
        ))

        """ Should keystrokes be echoed to stdout? """
        if 'echo' in self._task.args:
            try:
                echo = boolean(self._task.args['echo'])
            except TypeError as e:
                result['failed'] = True
                result['msg'] = to_native(e)
                return result

            # Add a note saying the output is hidden if echo is disabled
            if not echo:
                echo_prompt = ' (output is hidden)'

        # Is 'prompt' a key in 'args'?
        if 'prompt' in self._task.args:
            prompt = "[%s]\n%s%s:" % (self._task.get_name().strip(), self._task.args['prompt'], echo_prompt)
            if 'timeout_answer' in self._task.args:
                timeout_answer = str.encode(self._task.args['timeout_answer'])
        else:
            # If no custom prompt is specified, set a default prompt
            prompt = "[%s]\n%s%s:" % (self._task.get_name().strip(), 'Press enter to continue, Ctrl+C to interrupt', echo_prompt)

        # Does 'seconds' key exist in 'args'?
        if 'seconds' in self._task.args:
            try:
                seconds = int(self._task.args['seconds'])
                duration_unit = 'seconds'

            except ValueError as e:
                result['failed'] = True
                result['msg'] = u"non-integer value given for prompt duration:\n%s" % to_text(e)
                return result

        start = time.time()
        result['start'] = to_text(datetime.datetime.now())
        result['user_input'] = b''

        stdin_fd = None
        old_settings = None
        try:
            if seconds is not None and 'prompt' not in self._task.args:
                if seconds < 1:
                    seconds = 1

                # setup the alarm handler
                signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(seconds)

                # show the timer and control prompts
                display.display("Pausing for %d seconds%s" % (seconds, echo_prompt))
                display.display("(ctrl+C then 'C' = continue early, ctrl+C then 'A' = abort)\r"),

                # show the prompt specified in the task
                if 'prompt' in self._task.args:
                    display.display(prompt)

            else:
                if seconds is not None:
                    if seconds < 1:
                        seconds = 1
                    # setup the alarm handler
                    signal.signal(signal.SIGALRM, timeout_handler)
                    signal.alarm(seconds)
                    display.display("Pausing for %s seconds!" % seconds)

                display.display(prompt)

            # save the attributes on the existing (duped) stdin so
            # that we can restore them later after we set raw mode
            stdin_fd = None
            stdout_fd = None
            try:
                if PY3:
                    stdin = self._connection._new_stdin.buffer
                    stdout = sys.stdout.buffer
                else:
                    stdin = self._connection._new_stdin
                    stdout = sys.stdout
                stdin_fd = stdin.fileno()
                stdout_fd = stdout.fileno()
            except (ValueError, AttributeError):
                # ValueError: someone is using a closed file descriptor as stdin
                # AttributeError: someone is using a null file descriptor as stdin on windoez
                stdin = None

            if stdin_fd is not None:
                if isatty(stdin_fd):
                    # grab actual Ctrl+C sequence
                    try:
                        intr = termios.tcgetattr(stdin_fd)[6][termios.VINTR]
                    except Exception:
                        # unsupported/not present, use default
                        intr = b'\x03'  # value for Ctrl+C

                    # get backspace sequences
                    try:
                        backspace = termios.tcgetattr(stdin_fd)[6][termios.VERASE]
                    except Exception:
                        backspace = [b'\x7f', b'\x08']

                    old_settings = termios.tcgetattr(stdin_fd)
                    tty.setraw(stdin_fd)

                    # Only set stdout to raw mode if it is a TTY. This is needed when redirecting
                    # stdout to a file since a file cannot be set to raw mode.
                    if isatty(stdout_fd):
                        tty.setraw(stdout_fd)

                    # Only echo input if no timeout is specified
                    if echo:
                        new_settings = termios.tcgetattr(stdin_fd)
                        new_settings[3] = new_settings[3] | termios.ECHO
                        termios.tcsetattr(stdin_fd, termios.TCSANOW, new_settings)

                    # flush the buffer to make sure no previous key presses
                    # are read in below
                    termios.tcflush(stdin, termios.TCIFLUSH)

            while True:

                try:
                    if stdin_fd is not None:

                        key_pressed = stdin.read(1)

                        if key_pressed == intr:  # value for Ctrl+C
                            clear_line(stdout)
                            raise KeyboardInterrupt

                    """ if not seconds """
                    if stdin_fd is None or not isatty(stdin_fd):
                        display.warning("Not waiting for response to prompt as stdin is not interactive")
                        break

                    # read key presses and act accordingly
                    if key_pressed in (b'\r', b'\n'):
                        clear_line(stdout)
                        break
                    elif key_pressed in backspace:
                        # delete a character if backspace is pressed
                        result['user_input'] = result['user_input'][:-1]
                        clear_line(stdout)
                        if echo:
                            stdout.write(result['user_input'])
                        stdout.flush()
                    else:
                        result['user_input'] += key_pressed

                except KeyboardInterrupt:
                    signal.alarm(0)
                    display.display("Press 'C' to continue the play or 'A' to abort \r"),
                    if self._c_or_a(stdin):
                        clear_line(stdout)
                        break

                    clear_line(stdout)

                    raise AnsibleError('user requested abort!')

        except AnsibleTimeoutExceeded:
            if 'prompt' in self._task.args and 'timeout_answer' in self._task.args:
                result['user_input'] = timeout_answer
                clear_line(stdout)
                stdout.write(b'TimeoutExceeded. Timeout answer has been chosen: %s' % result['user_input'])
            else:
                # this is the exception we expect when the alarm signal
                # fires, so we simply ignore it to move into the cleanup
                pass
        finally:
            # cleanup and save some information
            # restore the old settings for the duped stdin stdin_fd
            if not(None in (stdin_fd, old_settings)) and isatty(stdin_fd):
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_settings)

            duration = time.time() - start
            result['stop'] = to_text(datetime.datetime.now())
            result['delta'] = int(duration)
            duration = round(duration, 2)
            result['stdout'] = "Paused for %s %s" % (duration, duration_unit)

        result['user_input'] = to_text(result['user_input'], errors='surrogate_or_strict')
        return result

    def _c_or_a(self, stdin):
        while True:
            key_pressed = stdin.read(1)
            if key_pressed.lower() == b'a':
                return False
            elif key_pressed.lower() == b'c':
                return True
