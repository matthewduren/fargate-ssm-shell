import time
import sys
import signal
import time
import os
import boto3
import botocore


class SsmShellFunctions():
    """Fake shell using ssm to run commands"""

    required_env_vars = ['CLUSTER', 'SERVICE', 'IAM_ROLE']

    def __init__(self):
        self.env = {}
        self.info = {}
        self._check_env()
        # Catch ctrl+c
        signal.signal(signal.SIGINT, self._sigint_cleanup)
        self.ssm_client = boto3.client('ssm')
        self.ecs_client = boto3.client('ecs')
        cur_unixtime = int(time.time())
        self.name = '{}-{}-{}'.format(self.env['CLUSTER'], self.env['SERVICE'], cur_unixtime)

    def _log(self, message, fatal=False):
        """Log, optionally exit"""
        print(message)
        if fatal:
            self._cleanup(message)
        return

    def _sigint_cleanup(self, signal, frame):
        """Cleanup task and ssm instance with interrupt failure reason"""
        self._cleanup('Interrupted by user')

    def _cleanup(self, reason='Completed successfully'):
        """Cleanup task and ssm instance"""
        self._log('Cleaning up, please do not interrupt this!')
        self._stop_task(reason)
        self._delete_ssm_activation()
        self._deregister_ssm_instance()
        self._log('Cleanup complete.')
        sys.exit(0)

    def _stop_task(self, reason: str):
        """Stop the task"""
        if 'task_arn' in self.info:
            self._log('Stopping ecs task...')
            kwargs = {
                'cluster': self.env['CLUSTER'],
                'task': self.info['task_arn'],
                'reason': reason
            }
            self.ecs_client.stop_task(**kwargs)
            self._log('Task stopped sucessfully.')

    def _check_env(self):
        """Check for required environment variables"""
        missing_vars = []
        for var in self.required_env_vars:
            value = os.getenv(var)
            if not value:
                missing_vars.append(var)
                continue
            self.env[var] = value
        if missing_vars:
            self._log('Missing required environment variables: {}'.format(missing_vars), True)
        return

    def _parse_task_definition(self):
        """Get info from the task definition"""
        task_def = self.ecs_client.describe_task_definition(taskDefinition=self.info['task_def'])
        self.info['container_name'] = task_def.get('taskDefinition', {}).get(
            'containerDefinitions', [{}])[0].get('name')
        return

    def _parse_ecs_service(self):
        """Get the task definition and security groups from a service"""
        try:
            kwargs = {
                'cluster': self.env['CLUSTER'],
                'services': [self.env['SERVICE']]
            }
            svc = self.ecs_client.describe_services(**kwargs)
            self.info['task_def'] = svc.get('services', [{}])[0].get('taskDefinition')
            if not self.info['task_def']:
                self._log('Task definition not found for service {}'.format(
                    self.env['SERVICE']), True)
            vpc_config = svc.get(
                'services', [{}])[0].get(
                    'networkConfiguration', {}).get(
                        'awsvpcConfiguration', {})
            self.info['security_groups'] = vpc_config.get('securityGroups', [])
            self.info['subnets'] = vpc_config.get('subnets', [])
        except botocore.exceptions.ClientError as err:
            if 'AccessDenied' in str(err):
                self._log('You do not have permission to perform this action!', True)
            else:
                raise
        self._parse_task_definition()
        return

    def _get_ssm_activation(self):
        """Get an ssm activation"""
        activation = self.ssm_client.create_activation(
            DefaultInstanceName=self.name,
            IamRole=self.env['IAM_ROLE'],
            RegistrationLimit=1
        )
        self.info['ssm_activation_id'] = activation['ActivationId']
        self.info['ssm_activation_code'] = activation['ActivationCode']
        return

    def _delete_ssm_activation(self):
        """Delete ssm activation"""
        if 'ssm_activation_id' in self.info:
            self._log('Deleting ssm activation...')
            self.ssm_client.delete_activation(ActivationId=self.info['ssm_activation_id'])
            self._log('Ssm activation deleted successfully.')

    def _start_task(self, started_by_name: str):
        """Start the task"""
        kwargs = {
            'cluster': self.env['CLUSTER'],
            'taskDefinition': self.info['task_def'],
            'overrides': {
                'containerOverrides': [
                    {
                        'name': self.info['container_name'],
                        'command': ['/bin/sh', '/opt/ssm-shell.sh'],
                        'environment': [
                            {
                                'name': 'CODE',
                                'value': self.info['ssm_activation_code']
                            },
                            {
                                'name': 'ID',
                                'value': self.info['ssm_activation_id']
                            }
                        ]
                    }
                ]
            },
            'count': 1,
            'startedBy': started_by_name,
            'launchType': 'FARGATE',
            'networkConfiguration': {
                'awsvpcConfiguration': {
                    'subnets': self.info['subnets'],
                    'securityGroups': self.info['security_groups'],
                    'assignPublicIp': 'DISABLED'
                }
            }
        }
        # Retry a few times if we get a bad task definition.  Maybe we're deploying...
        timeout_count = 5
        elapsed = 0
        while True:
            if elapsed >= timeout_count:
                self._log('Failed to retrieve task definition.', True)
            try:
                task = self.ecs_client.run_task(**kwargs)
                break
            except botocore.exceptions.ClientError as err:
                if 'TaskDefinition is inactive' in str(err):
                    time.sleep(10)
            elapsed += 1
        task_arn = task.get('tasks', [{}])[0].get('taskArn')
        if not task_arn:
            self._log('Failed to start task.', True)
        self.info['task_arn'] = task_arn
        # Wait for task to run
        waiter = self.ecs_client.get_waiter('tasks_running')
        self._log("Waiting for task to start. Please don't flame.")
        try:
            waiter.wait(cluster=self.env['CLUSTER'], tasks=[task_arn])
        except botocore.exceptions.WaiterError as err:
            self._log('Task failed to start!', True)
        return
 
    def _get_ssm_instance(self) -> str:
        """Get instances"""
        paginator = self.ssm_client.get_paginator('describe_instance_information')
        instances = []
        for page in paginator.paginate():
            instances.extend(page.get('InstanceInformationList', []))
        selected_instances = [i['InstanceId'] for i in instances if i.get('Name', '') == self.name]
        if len(selected_instances) > 1:
            self._log('Too many SSM instances', True)
        if not selected_instances:
            selected_instances = [None]
        instance_id = selected_instances[0]
        return instance_id

    def _wait_ssm_instance(self, timeout: int=2):
        """Wait for the instance to show up in SSM"""
        timeout_sec = timeout * 60
        wait = 10
        elapsed = 0
        while True:
            if elapsed >= timeout_sec:
                self._log('Timeout waiting for ssm instance to register.', True)
            self._log('Waiting up to {} minutes for instance to become available...'.format(
                str(timeout)))
            time.sleep(wait)
            elapsed += wait
            instance = self._get_ssm_instance()
            if instance:
                break
        self.info['ssm_instance'] = instance
        return

    def _deregister_ssm_instance(self):
        """Deregister the ssm instance"""
        if 'ssm_instance' in self.info:
            self._log('Deregistering ssm instance...')
            self.ssm_client.deregister_managed_instance(InstanceId=self.info['ssm_instance'])
            self._log('Ssm instance deregistered successfully.')
        return

    def _get_ssm_command_output(self, command_id: str) -> dict:
        """Retrieve the command output, if any"""
        kwargs = {
            'CommandId': command_id,
            'InstanceId': self.info['ssm_instance']
        }
        try:
            invocation = self.ssm_client.get_command_invocation(**kwargs)
        except botocore.exceptions.ClientError as err:
            if 'InvocationDoesNotExist' in str(err):
                invocation = {}
        if invocation.get('StatusDetails', 'Pending') in ['Pending', 'InProgress']:
            return None
        stdout = invocation.get('StandardOutputContent', '')
        stderr = invocation.get('StandardErrorContent', '')
        output = {
            'stdout': stdout,
            'stderr': stderr
        }
        return output

    def _wait_ssm_command_complete(self, command_id: str) -> dict:
        """Waits for the ssm command to complete."""
        timeout_sec = 60
        wait = 1
        elapsed = 0
        while True:
            time.sleep(wait)
            if elapsed >= timeout_sec:
                self._log('Timeout waiting for ssm instance to register.', True)
            print('.', end='')
            output = self._get_ssm_command_output(command_id)
            if output:
                break
        # Drop in a newline
        print('')
        self._log('Result: {}'.format(output['stdout']))
        self._log('Errors: {}'.format(output['stderr']))
        return output

    def _run_ssm_command(self, stdin: str):
        """Run an ssm command against the instance"""
        if stdin == 'exit':
            self._cleanup('Exited')
        kwargs = {
            'InstanceIds': [self.info['ssm_instance']],
            'DocumentName': 'AWS-RunShellScript',
            'Parameters': {
                'commands': [stdin]
            }
        }
        command = self.ssm_client.send_command(**kwargs)
        command_id = command.get('Command', {}).get('CommandId')
        if not command_id:
            self._log('Command failed to start.')
            self._cleanup('Ssm command failed to start')
        self._wait_ssm_command_complete(command_id)
