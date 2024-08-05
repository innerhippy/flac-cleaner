from __future__ import annotations
import re
import tempfile
import os
from termcolor import colored
import gitlab
from gitlab.v4.objects.projects import Project
from gitlab.v4.objects.groups import Group
from gitlab.exceptions import GitlabGetError
import subprocess
import click
from dateutil import parser
from contextlib import contextmanager
import yaml

POST_UPDATE_HOOK = '''#!/bin/sh

echo "Pushing to Gitlab mirror"

git push -f --mirror %(url)s

'''

PRE_RECEIVE_HOOK = '''
#!/bin/sh

cat<< EOM
*************************************************************************
*
*  This project has moved to Gitlab
*
*  Please update your project URL using:
*  git remote set-url origin %(url)s
*
*************************************************************************
EOM

exit 1
'''


class GroupNotFoundException(Exception):
    pass


class ProjectNotFoundException(Exception):
    pass


class Manager(object):
    ''' Class to manage Framestore Gitlab repositories.

        - creating projects
        - validating project settings
        - adding hooks from legacy filesystem repos to:
            - mirror repo to Gitlab
            - block any commits
    '''
    __ROOT_GROUP = 'Framestore'

    PROJECT_FIELDS = [
        ('Project path', None),
        ('Description', ''),
        ('Created By', ''),
        ('Last Actvity', ''),
        ('Branches', 0),
        ('Commits', 0),
        ('Open MRs', 0)
    ]

    ACCESS_MAP = {
        gitlab.const.DEVELOPER_ACCESS: 'developer',
        gitlab.const.GUEST_ACCESS: 'guest',
        gitlab.const.MAINTAINER_ACCESS: 'maintainer',
        gitlab.const.NO_ACCESS: 'none',
        gitlab.const.OWNER_ACCESS: 'owner',
        gitlab.const.MINIMAL_ACCESS: 'minimal',
        gitlab.const.REPORTER_ACCESS: 'reporter',
    }

    def __init__(self, gitlab_token, dry_run=False, config=None):
        self._dry_run = dry_run
        self._gl = gitlab.Gitlab(
            "https://gitlab.com", private_token=gitlab_token
        )
        self._configfile = config
        self._configdata = None
        self._user_group_cache = None

    def _config(self, value):
        if self._configdata is None:
            self._configdata = self._load_config(self._configfile) or {}

        return self._configdata.get(value)

    def _root_group(self, group=None):
        '''Returns group path prefixed by Framestore, or just Framestore if no group provided
        '''
        groups = group.split('/') if group else [self.__ROOT_GROUP]
        if groups[0].lower() != self.__ROOT_GROUP.lower():
            groups.insert(0, self.__ROOT_GROUP)
        return '/'.join(groups)

    @property
    def users(self):
        return self._config('users') or []

    @staticmethod
    def error(msg):
        click.echo(colored("- " + msg, 'red'))

    @staticmethod
    def warn(msg):
        click.echo(colored("- " + msg, 'yellow'))

    @staticmethod
    def okay(msg):
        click.echo(colored("- " + msg, 'green'))

    @staticmethod
    def info(msg):
        click.echo("- " + msg)

    @staticmethod
    def split_path(path):
        ''' Splits a group/repo path into group and repo parts.
            Any .git suffix is dropped from the repo name
        '''
        match = re.match(r'^(?:([\w/]+)/)?([\w-]+)(?:\.git)?$', path)
        if not match:
            raise Exception('Project name must be lowercase and dashes only')

        group, name = match.groups()
        return group, name

    @staticmethod
    def validate_project_name(name):
        ''' Ensure project name conforms to naming standards - lowercase and dashes
        '''
        assert re.match(r'^[a-z-]+$', name), f'{name} needs to be lowercase and dashes only'

    @classmethod
    def _get_access_level_for_members(cls, username, members):
        for name, access_level in members:
            if username == name:
                return access_level

    def user_group_membership(self, user):
        if self._user_group_cache is None:
            self._user_group_cache = {}
            for group in self.walk_groups('framestore/users'):
                self._user_group_cache[group] = [
                    (user.username, user.access_level,) for user in group.members.list(all=True)
                ]

        for group, members in self._user_group_cache.items():
            access_level = self._get_access_level_for_members(user.username, members)
            if access_level:
                yield f'{group.name} ({self.ACCESS_MAP[access_level]})'

    def get_group(self, group=None):
        ''' Returns a Gitlab group object given a group name or path. The default group is prepended.
        '''
        if isinstance(group, gitlab.v4.objects.groups.GroupSubgroup):
            # Already a Gitlab group object
            return self._gl.groups.get(group.id)
        elif isinstance(group, dict) and 'id' in group:
            return self._gl.groups.get(group['id'])
        elif isinstance(group, int):
            return self._gl.groups.get(group)

        try:
            return self._gl.groups.get(self._root_group(group))
        except GitlabGetError:
            raise GroupNotFoundException(f"Cannot find group {group!r}")

    def walk_groups(self, group, maxdepth=None):
        ''' A generator to walk groups recursively.
            Yields gitlab group object
        '''
        if isinstance(maxdepth, int):
            if maxdepth == 0:
                return
            maxdepth -= 1

        grp = self.get_group(group)
        yield grp

        for subgroup in grp.subgroups.list(all=True):
            yield from self.walk_groups(subgroup, maxdepth)

    def parse_path(self, name: str) -> tuple[Group, Project]:
        """ If name is a group path, return the Gitlab group object and None as project.
            If name also contains a project, also return the Gitlab project object
        """
        project = None
        try:
            group = self.get_group(name)
        except GroupNotFoundException:
            paths = name.split("/")
            new_group_path = "/".join(paths[:-1])
            group = self.get_group(new_group_path)
            project = self.get_project(paths[-1], group)

        return group, project

    def walk_projects(self, name, include_groups=False, exclude=None):
        ''' A generator to walk projects recursively.
            Yields gitlab project object
        '''
        group, project = self.parse_path(name)

        if project:
            yield project
        else:
            if include_groups:
                yield group
            for project in group.projects.list(all=True, with_shared=False, archived=False):
                yield self._gl.projects.get(project.id)

            for subgroup in group.subgroups.list(all=True):
                yield from self.walk_projects(subgroup, include_groups=include_groups)

    def get_project_details(self, project):
        ''' Condense gitlab project details into relevent fields
        '''
        return dict(
            zip(
                (f[0] for f in self.PROJECT_FIELDS),
                (
                    project.path_with_namespace,
                    project.description or '',
                    self._gl.users.get(project.creator_id).name,
                    parser.parse(project.last_activity_at).strftime('%Y-%m-%d') or '',
                    len(project.branches.list(all=True)) or 0,
                    len(project.commits.list(all=True)) or 0,
                    len(project.mergerequests.list(all=True, state='opened')) or 0,
                )
            )
        )

    def get_project(self, name: str, group: Group) -> Project:
        ''' Returns a Gitlab Project object for project 'name' within a group
        '''
        for project in group.projects.list(all=True, with_shared=False, archived=False):
            if project.path.lower() == name.lower():
                return self._gl.projects.get(project.id)

        raise ProjectNotFoundException(f"Cannot find project {name!r}")

    def check_master_protect(self, project: Project) -> bool:
        ''' Ensure master branch is protected. No direct pushes allowed.
        '''
        for branch in project.protectedbranches.list():
            if branch.name == 'master':
                for access in branch.merge_access_levels:
                    if access["access_level"] != gitlab.DEVELOPER_ACCESS:
                        self.error(
                            f"master merge access set to {access['access_level_description']!r}, "
                            f"expecting 'Developers + Maintainers'"
                        )
                        return False
                for access in branch.push_access_levels:
                    if access["access_level"] != gitlab.NO_ACCESS:
                        self.error(
                            f"master push access set to {access['access_level_description']!r}, "
                            f"expecting 'No one'"
                        )
                        return False
        return True

    def set_master_protect(self, project):
        ''' Ensure master branch is protected. No direct pushes allowed.
        '''
        if not self.check_master_protect(project):
            click.echo(
                f"- creating master branch protection to push: "
                f"'No one', merge: 'Developers + Maintainers'{self._debug_str}"
            )
            if not self._dry_run:
                project.protectedbranches.create(
                    {
                        'name': 'master',
                        'allowed_to_push': [{"access_level": gitlab.NO_ACCESS}],
                        'allowed_to_merge': [{"access_level": gitlab.DEVELOPER_ACCESS}],
                    }
                )

    def check_merge_request_approvals(self, project):
        ''' Sets required merge requests approvals to 1 if currently zero
        '''
        project_settings = {
            'merge_method': 'merge',
            'only_allow_merge_if_all_discussions_are_resolved': True,
            'only_allow_merge_if_pipeline_succeeds': True,
            'remove_source_branch_after_merge': True,
        }

        approval_settings = {
            'merge_requests_author_approval': False,
            'merge_requests_disable_committers_approval': True,
            'require_password_to_approve': False,
            'reset_approvals_on_push': True,
        }

        # Check project merge settings
        for key, val in project_settings.items():
            attr = getattr(project, key)
            if attr != val:
                self.error(f"expecting {key!r} as {val!r}, got {attr!r}")

        # Check approval settings
        settings = project.approvals.get(all=True)
        for key, val in approval_settings.items():
            attr = getattr(settings, key)
            if attr != val:
                self.error(f"expecting {key!r} as {val!r}, got {attr!r}")

        # Check approval rules
        approval_rules = project.approvalrules.list()
        if not approval_rules:
            self.error("no approval rules")

        for p_mras in approval_rules:
            if p_mras.approvals_required == 0:
                self.error('required approvals is zero')
            else:
                self.okay(f"approval rule {p_mras.name!r} ok")

    def set_merge_request_approvals(self, project):
        ''' Sets required merge requests approvals to 1 if currently zero
        '''
        settings = {
            'merge_method': 'merge',
            'only_allow_merge_if_all_discussions_are_resolved': True,
            'remove_source_branch_after_merge': True,
        }
        to_write = settings.copy()

        # Check project merge settings
        for key, val in settings.items():
            attr = getattr(project, key)
            if attr == val:
                # Remove from settings list to write
                del to_write[key]
            elif self._dry_run:
                click.echo(
                    f"- attribute {key!r}: expecting {val!r}, got {attr!r}"
                )

        if to_write:
            click.echo(
                f'- updating merge attributes {to_write}{self._debug_str}'
            )
            if not self._dry_run:
                self._gl.http_put(
                    f'/projects/{project.id}', post_data=to_write
                )

        # Check approval rules
        for p_mras in project.approvalrules.list():
            if p_mras.name == 'Default':
                if p_mras.approvals_required > 0:
                    if not self._dry_run:
                        p_mras.approvals_required = 1
                break
        else:
            self._add_approval_rules(project)

    def check_slack_notifications(self, project):
        ''' Set slack notification on merge requests
        '''
        slack = self._config('slack')
        expected_url = slack.get('webhook') if slack else None

        try:
            slack = project.services.get('slack')
        except GitlabGetError:
            self.error("Slack integration not found")
            return

        if not slack.active:
            self.error("Slack integration not found")
            return

        configured_url = slack.properties.get('webhook')

        if expected_url is None:
            self.okay(f"Slack notification {configured_url!r}")
        elif expected_url == configured_url:
            # All good
            self.okay("Slack notification matches config")
        else:
            self.warn(f"Slack notification mismatch. Expected {expected_url!r}, got {configured_url!r}")

    def set_slack_notifications(self, project):
        ''' Set slack notification on merge requests
        '''
        try:
            slack = self._config('slack')
            if slack is None:
                click.echo(f"Slack integration not found on project {project.name!r}")
                return
            webhook = slack.get('webhook')
        except KeyError:
            click.echo("no slack webhook configured")
            return

        try:
            slack = project.services.get('slack')
        except GitlabGetError:
            click.echo(f"Slack integration not found on project {project.name!r}")
            return

        if slack.active:
            if slack.properties.get('webhook') == webhook:
                # All good
                return

        click.echo(
            f'- creating slack integration for merge requests{self._debug_str}'
        )
        if not self._dry_run:
            data = {
                'webhook': webhook,
                'branches_to_be_notified': 'all',
                'notify_only_broken_pipelines': False,
                'push_events': False,
                'issues_events': False,
                'confidential_issues_events': False,
                'merge_requests_events': True,
                'note_events': False,
                'confidential_note_events': False,
                'tag_push_events': False,
                'pipeline_events': False,
                'wiki_page_events': False,
                'deployment_events': False,
                'job_events': True,
                'commit_events': False,
            }
            # Python API doesn't seem to work
            self._gl.http_put(f'/projects/{project.id}/services/slack', post_data=data)

    def create_project(self, name, group):
        ''' Creates a nre Gitlab project under a given group
        '''
        if not self._dry_run:
            return self._gl.projects.create(
                {
                    'name': name,
                    'namespace_id': group.id
                }
            )

    def _load_config(self, config):
        ''' Load a yaml config file containg email addresses to set as reviwers
            for new projects
        '''
        if config:
            with open(config) as fp:
                return yaml.load(fp, Loader=yaml.Loader)

    def _add_approval_rules(self, project):
        ''' Add a new approval rule called Default, with a list of
            users from which one needs to approve any merge request
        '''
        click.echo(f"- adding 'Default' approval rule{self._debug_str}")
        if not self._dry_run:
            user_ids = self.get_users_ids(self.users)
            project.approvalrules.create(
                {
                    'name': 'Default',
                    'approvals_required': 1,
                    'rule_type': 'regular',
                    'user_ids': user_ids
                    }
            )

    def get_users_ids(self, users):
        ''' Translate from email address into a list of Gitlab user IDs
        '''
        return [self._gl.users.list(search=user)[0].id for user in users]

    @classmethod
    @contextmanager
    def _mount_sshfs(cls, path):
        ''' Context manager to mount a temp directory to
            a git repo on a remote SSH server, using sshfs
            If path doesn't contain an SSH host then no sshfs mount takes place
        '''
        local_path = ':' not in path

        try:
            if local_path:
                yield path
            else:
                temp_dir = tempfile.mkdtemp()
                cls._run_command(['sshfs', path, temp_dir])
                yield temp_dir
        finally:
            if not local_path:
                cls._run_command(['fusermount', '-u', temp_dir])
                os.rmdir(temp_dir)

    def write_mirror_hook(self, project, git_path):
        ''' Writes the post-update hook to legacy git_path to
            mirror the repo to Gitlab, and run the hook
        '''
        with self._mount_sshfs(git_path) as mount_point:
            self._write_mirror_hook(project.ssh_url_to_repo, mount_point)
            click.echo(
                f"Mirroring {git_path!r} {project.web_url!r} "
                f"to Gitlab{self._debug_str}"
            )
            if not self._dry_run:
                self._run_command(
                    ['git', 'push', '-f', '--mirror', project.ssh_url_to_repo],
                    path=mount_point
                )

    def write_reject_push_hook(self, project, git_path):
        ''' Writes the pre-receive hook to legacy git_path to
            prevent any futher pushes to the legacy repo
        '''
        with self._mount_sshfs(git_path) as mount_point:
            self._write_reject_push_hook(project.ssh_url_to_repo, mount_point)

    def _write_mirror_hook(self, url, path):
        ''' Write the post-update to legacy git repo to
            mirror to the new Gitlab project
        '''
        hook = os.path.join(path, 'hooks', 'post-update')
        if os.path.exists(hook):
            click.echo(f'Warning: post-update hook already exists for {url}. Will not overwrite')
            return

        click.echo(f"Adding post-update hook for {url!r}{self._debug_str}")
        if not self._dry_run:
            with open(hook, 'w') as fp:
                fp.write(POST_UPDATE_HOOK % {'url': url})

            # Set execution permissions
            os.chmod(hook, 0o755)

    def _write_reject_push_hook(self, url, path):
        ''' Write the pre-receive hook to legacy git_path to
            prevent any futher pushes to the legacy repo
        '''
        hook = os.path.join(path, 'hooks', 'pre-receive')
        if os.path.exists(hook):
            click.echo(
                'Warning: pre-receive hook already exists for '
                f'{url}. Will not overwrite'
            )
            return

        click.echo(f"Adding pre-receive hook for {url!r}{self._debug_str}")
        if not self._dry_run:
            with open(hook, 'w') as fp:
                fp.write(PRE_RECEIVE_HOOK % {'url': url})

            # Set execution permissions
            os.chmod(hook, 0o755)

    @classmethod
    def _run_command(cls, command, path=None):
        ''' Run an arbitrary commmand and raise if exit status is non-zero
        '''
        if isinstance(command, str):
            command = command.split()

        process = subprocess.Popen(
            command,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=path
        )
        stdout, stderr = process.communicate()
        if process.returncode:
            raise Exception(stderr)
        return stdout.strip()

    @property
    def _debug_str(self):
        return " - DRY RUN" if self._dry_run else ""
