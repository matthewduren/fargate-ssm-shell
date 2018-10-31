"""
Launch a fargate container and send commands via SSM

Todo: N/A
"""

from functions import SsmShellFunctions

STARTED_BY_NAME = 'ssm_shell'


class SsmShell(SsmShellFunctions):
    """Launch a fargate container and send commands via SSM"""

    def connect(self):
        """Connect to the target app based on ecs_service name"""
        self._parse_ecs_service()
        self._get_ssm_activation()
        self._start_task(STARTED_BY_NAME)
        self._wait_ssm_instance()
        self.command_listener()

    def command_listener(self):
        """Prompt for commands then process input"""
        self._log('Ready for commands.')
        while True:
            try:
                stdin = input('> ')
                self._run_ssm_command(stdin)
            except EOFError:
                pass
        return

if __name__ == '__main__':
    SHELL = SsmShell()
    SHELL.connect()
