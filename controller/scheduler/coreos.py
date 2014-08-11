from cStringIO import StringIO
import base64
import os
import random
import re
import subprocess
import time


ROOT_DIR = os.path.join(os.getcwd(), 'coreos')
if not os.path.exists(ROOT_DIR):
    os.mkdir(ROOT_DIR)

MATCH = re.compile('(?P<app>[a-z0-9-]+)_?(?P<version>v[0-9]+)?\.?(?P<c_type>[a-z]+)?.(?P<c_num>[0-9]+)')

class FleetClient(object):

    def __init__(self, cluster_name, hosts, auth, domain, options):
        self.name = cluster_name
        self.hosts = hosts
        self.domain = domain
        self.options = options
        self.auth = auth
        self.auth_path = os.path.join(ROOT_DIR, 'ssh-{cluster_name}'.format(**locals()))
        with open(self.auth_path, 'w') as f:
            f.write(base64.b64decode(auth))
            os.chmod(self.auth_path, 0600)

        self.env = {
            'PATH': '/usr/local/bin:/usr/bin:/bin:{}'.format(
                os.path.abspath(os.path.join(__file__, '..'))),
            'FLEETW_KEY': self.auth_path,
            'FLEETW_HOST': random.choice(self.hosts.split(','))}

    # scheduler setup / teardown

    def setUp(self):
        """
        Setup a CoreOS cluster including router and log aggregator
        """
        return

    def tearDown(self):
        """
        Tear down a CoreOS cluster including router and log aggregator
        """
        return

    # job api

    def create(self, name, image, command='', template=None, **kwargs):
        """
        Create a new job
        """
        print 'Creating {name}'.format(**locals())
        env = self.env.copy()
        self._create_container(name, image, command, template or CONTAINER_TEMPLATE, env, **kwargs)
        self._create_log(name, image, command, LOG_TEMPLATE, env)

    def _create_container(self, name, image, command, template, env, **kwargs):
        l = locals().copy()
        l.update(re.match(MATCH, name).groupdict())
        # prepare memory limit for the container type
        mem = kwargs.get('memory', {}).get(l['c_type'], None)
        if mem:
          l.update({'memory': '-m {}'.format(mem.lower())})
        else:
          l.update({'memory': ''})
        # prepare memory limit for the container type
        cpu = kwargs.get('cpu', {}).get(l['c_type'], None)
        if cpu:
          l.update({'cpu': '-c {}'.format(cpu)})
        else:
          l.update({'cpu': ''})
        env.update({'FLEETW_UNIT': name + '.service'})
        env.update({'FLEETW_UNIT_DATA': base64.b64encode(template.format(**l))})
        return subprocess.check_call('fleetctl.sh submit {name}.service'.format(**l),
                                     shell=True, env=env)

    def _create_log(self, name, image, command, template, env):
        l = locals().copy()
        l.update(re.match(MATCH, name).groupdict())
        env.update({'FLEETW_UNIT': name + '-log' + '.service'})
        env.update({'FLEETW_UNIT_DATA': base64.b64encode(template.format(**l))})
        return subprocess.check_call('fleetctl.sh submit {name}-log.service'.format(**locals()),  # noqa
                                     shell=True, env=env)

    def start(self, name):
        """
        Start an idle job
        """
        print 'Starting {name}'.format(**locals())
        env = self.env.copy()
        self._start_container(name, env)
        self._start_log(name, env)

        self._wait_for_container(name, env)

    def _start_log(self, name, env):
        subprocess.check_call(
            'fleetctl.sh start -no-block {name}-log.service'.format(**locals()),
            shell=True, env=env)

    def _start_container(self, name, env):
        return subprocess.check_call(
            'fleetctl.sh start -no-block {name}.service'.format(**locals()),
            shell=True, env=env)

    def _wait_for_container(self, name, env):
        status = None
        # we bump to 20 minutes here to match the timeout on the router and in the app unit files
        for _ in range(1200):
            status = subprocess.check_output(
                "fleetctl.sh list-units --no-legend --fields unit,sub | grep {name}.service | awk '{{print $2}}'".format(**locals()),
                shell=True, env=env).strip('\n')
            if status == 'running':
                break
            time.sleep(1)
        else:
            raise RuntimeError('Container failed to start')

    def stop(self, name):
        """
        Stop a running job
        """
        print 'Stopping {name}'.format(**locals())
        env = self.env.copy()

        self._stop_container(name, env)
        self._stop_log(name, env)

    def _stop_container(self, name, env):
        return subprocess.check_call(
            'fleetctl.sh stop -block-attempts=600 {name}.service'.format(**locals()),
            shell=True, env=env)

    def _stop_log(self, name, env):
        return subprocess.check_call(
            'fleetctl.sh stop -block-attempts=600 {name}-log.service'.format(**locals()),
            shell=True, env=env)

    def destroy(self, name):
        """
        Destroy an existing job
        """
        print 'Destroying {name}'.format(**locals())
        env = self.env.copy()

        self._destroy_container(name, env)
        self._destroy_log(name, env)

    def _destroy_container(self, name, env):
        return subprocess.check_call(
            'fleetctl.sh destroy {name}.service'.format(**locals()),
            shell=True, env=env)

    def _destroy_log(self, name, env):
        return subprocess.check_call(
            'fleetctl.sh destroy {name}-log.service'.format(**locals()),
            shell=True, env=env)

    def run(self, name, image, command):
        """
        Run a one-off command
        """
        print 'Running {name}'.format(**locals())
        output = subprocess.PIPE
        p = subprocess.Popen('fleetrun.sh {command}'.format(**locals()), shell=True, env=self.env,
                             stdout=output, stderr=subprocess.STDOUT)
        rc = p.wait()
        return rc, p.stdout.read()

    def attach(self, name):
        """
        Attach to a job's stdin, stdout and stderr
        """
        return StringIO(), StringIO(), StringIO()

SchedulerClient = FleetClient


CONTAINER_TEMPLATE = """
[Unit]
Description={name}

[Service]
ExecStartPre=/bin/sh -c "IMAGE=$(etcdctl get /deis/registry/host 2>&1):$(etcdctl get /deis/registry/port 2>&1)/{image}; docker pull $IMAGE"
ExecStartPre=/bin/sh -c "docker inspect {name} >/dev/null 2>&1 && docker rm -f {name} || true"
ExecStart=/bin/sh -c "IMAGE=$(etcdctl get /deis/registry/host 2>&1):$(etcdctl get /deis/registry/port 2>&1)/{image}; port=$(docker inspect -f '{{{{range $k, $v := .ContainerConfig.ExposedPorts }}}}{{{{$k}}}}{{{{end}}}}' $IMAGE | cut -d/ -f1) ; docker run --name {name} {memory} {cpu} -P -e PORT=$port $IMAGE {command}"
ExecStop=/usr/bin/docker rm -f {name}
TimeoutStartSec=20m
"""

LOG_TEMPLATE = """
[Unit]
Description={name} log
BindsTo={name}.service

[Service]
ExecStartPre=/bin/sh -c "until docker inspect {name} >/dev/null 2>&1; do sleep 1; done"
ExecStart=/bin/sh -c "docker logs -f {name} 2>&1 | logger -p local0.info -t {app}[{c_type}.{c_num}] --udp --server $(etcdctl get /deis/logs/host) --port $(etcdctl get /deis/logs/port)"
TimeoutStartSec=20m

[X-Fleet]
X-ConditionMachineOf={name}.service
"""
