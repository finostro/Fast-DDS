# Copyright 2026 Proyectos y Sistemas de Mantenimiento SL (eProsima)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Check how a server handles the participants relayed by another server when the latter is removed.

Server A and server B are connected, and a client only knows server A, so server B only knows the client
through server A.
- If server A and the client die, server B must drop the client after a hold-down of one lease duration of
  server A since server A is removed.
- If server A dies and is restarted while the client is alive, server B must not drop the client.
"""

import glob
import os
import signal
import subprocess
import sys
import threading
import time

script_dir = os.path.dirname(os.path.realpath(__file__))

# Test executable
process_command = os.environ.get('CLIENT_SERVER_DYNAMIC_DISCOVERY_BIN')
if not process_command:
    process_files = glob.glob(
        os.path.join(
           script_dir,
           '**/DDSParticipantDiscovery*'),
        recursive=True)
    pf = iter(process_files)
    process_command = next(pf, None)
    while process_command and \
        (not os.path.isfile(process_command)
         or not os.access(process_command,
         os.X_OK)):
        process_command = next(pf, None)
assert(process_command)

base_port = int(os.environ.get('W_UNICAST_PORT_RANDOM_NUMBER', '11811'))
server_a_port = base_port
server_b_port = base_port + 1

server_a_prefix = '44.53.00.5f.45.50.52.4f.53.49.4d.41'
server_b_prefix = '44.53.01.5f.45.50.52.4f.53.49.4d.41'
server_a_guid = server_a_prefix + '|0.0.1.c1'

# Short leases to keep the test duration low
lease_duration = 4
announcement_period = 1
lease_args = ['--lease_duration', str(lease_duration), '--announcement_period', str(announcement_period)]


class Participant:
    """Run a DDSParticipantDiscovery process and collect its output."""

    def __init__(self, name, args, ros_discovery_server):
        self.name = name
        self.args = args
        self.ros_discovery_server = ros_discovery_server
        self.lines = []
        self.condition = threading.Condition()
        self.process = None
        self.start()

    def start(self):
        env = os.environ.copy()
        env['ROS_DISCOVERY_SERVER'] = self.ros_discovery_server
        env.pop('FASTDDS_ENVIRONMENT_FILE', None)
        self.process = subprocess.Popen(
            [process_command] + self.args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env)
        threading.Thread(target=self._read, args=(self.process,), daemon=True).start()

    def _read(self, process):
        for raw in iter(process.stdout.readline, b''):
            line = raw.decode('utf-8').rstrip()
            print('[{}] {}'.format(self.name, line))
            sys.stdout.flush()
            with self.condition:
                self.lines.append((time.time(), line))
                self.condition.notify_all()

    def wait_for(self, predicate, timeout):
        """Return (time, line) of the first line matching predicate, or None on timeout."""
        deadline = time.time() + timeout
        with self.condition:
            while True:
                for entry in self.lines:
                    if predicate(entry[1]):
                        return entry
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self.condition.wait(remaining)

    def crash(self):
        self.process.kill()
        self.process.wait()

    def stop(self):
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


def server_a():
    # Server A connects to server B, which is the second one in the list
    return Participant(
        'server A',
        ['--discovery_protocol', 'SERVER', '--guid_prefix', server_a_prefix,
         '--unicast_metatraffic_locator', str(server_a_port)] + lease_args,
        ';127.0.0.1:{}'.format(server_b_port))


def setup():
    """Launch both servers and a client of server A. Return them and the client GUID."""
    server_b = Participant(
        'server B',
        ['--discovery_protocol', 'SERVER', '--guid_prefix', server_b_prefix,
         '--unicast_metatraffic_locator', str(server_b_port)] + lease_args,
        '127.0.0.1:{}'.format(server_a_port))
    server = server_a()
    # A SIMPLE participant becomes a client of server A through the environment variable
    # Servers only relay participants with endpoints
    client = Participant('client', lease_args + ['--topic', 'DropRelayedParticipantsTopic'],
                         '127.0.0.1:{}'.format(server_a_port))

    # Server B knows the client only through server A
    entry = server_b.wait_for(
        lambda line: 'discovered participant' in line and 'undiscovered' not in line and
        '44.53.' not in line.split('discovered participant')[1], 20)
    if entry is None:
        stop_all([server_b, server, client])
        fail('server B did not discover the client of server A')
    client_guid = entry[1].split('discovered participant ')[1].split(':')[0]
    print('Client of server A: ' + client_guid)
    return server_b, server, client, client_guid


def stop_all(participants):
    for participant in participants:
        participant.stop()


def fail(message):
    print('ERROR: ' + message)
    sys.exit(1)


def undiscovered(guid):
    return lambda line: 'undiscovered participant ' + guid in line


def test_dropped_when_server_and_client_die():
    print('##### Server A and its client die')
    server_b, server, client, client_guid = setup()

    crash_time = time.time()
    server.crash()
    client.crash()

    # Server A is dropped after its lease, and its client after one more lease
    entry = server_b.wait_for(undiscovered(client_guid), 3 * lease_duration + 5)
    stop_all([server_b])
    if entry is None:
        fail('server B did not drop the client of the dead server A')
    if server_b.wait_for(undiscovered(server_a_guid), 0) is None:
        fail('server B did not drop server A')
    elapsed = entry[0] - crash_time
    print('Client dropped {:.1f} s after the crash'.format(elapsed))
    if elapsed < lease_duration:
        fail('client dropped before the hold-down expired')


def test_kept_when_server_restarts():
    print('##### Server A dies and restarts while its client is alive')
    server_b, server, client, client_guid = setup()

    server.crash()

    # Restart server A once server B has dropped it, so the client must be relayed again within the hold-down
    if server_b.wait_for(undiscovered(server_a_guid), 3 * lease_duration) is None:
        stop_all([server_b, client])
        fail('server B did not drop server A')
    server = server_a()

    # Wait beyond the hold-down
    dropped = server_b.wait_for(undiscovered(client_guid), 2 * lease_duration + 2)
    stop_all([server, client, server_b])
    if dropped is not None:
        fail('server B dropped the alive client of the restarted server A')


test_dropped_when_server_and_client_die()
test_kept_when_server_restarts()
print('Test successful')
sys.exit(0)
