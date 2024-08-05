# fs-gitlab

## About

Python3 utility to manage Framestore Gitlab projects. Written to migrate Sydev projects from filesystem git to hosted Gitlab, but no reason why this cannot be used outside the Sysdev group.

The utility provides a number of commands:

* `create`  - create a new projects in Gitlab
* `check`   - runs a number of checks against all projects under a group
* `mirror`  - installs a post-update hook on a project at git.framestore.com:/mnt/scm/git/...  to mirror to Gitlab
* `lock`    - installs a pre-receive hook on a project at git.framestore.com:/mnt/scm/git/...  to block any new pushes
* `migrate` - runs `create`, `mirror` and `lock` in single command.
* `runners`  - list the runners for a group
* `list`    - display table of projects stats

## Authorisation

You will need a Gitlab access token to authenticate yourself with the Gitlab API.

On Gitlab, go to your Settings page and select Access Tokens. Create a new token with the following permissions:

* `api`
* `write_repository`

Save the token in your .bashrc file as

`export GITLAB_PRIVATE_TOKEN=<access_token>`

## Actions

When creating a new project, a number of checks and configuration changes are made.

### Project name

This utility enforces project name to be lower-case and dashes only. Not because this is necesserily *the* correct way to name projects, more that it's just *a* way so that we can stick to one convention and not have a confusing mix.

### Master branch protection

Master branch is protected so that it is not possible to push directly. This is sensible because it enforces a workflow in which all work to be merged must go through a review procedure. Even if you are pushing out a hot fix on Sunday evening with nobody else around, you can create a merge request, assign it to you and approve it yourself. This policy also prevents accidental master branch pushes, which happen all too often.

Gitlab will only permit master branch merges from users who have Developers or Maintainers roles.

### Merge Requests

There are a number of settings that govern the behaviour of MRs.

1. Each merge to master generates a separate commit, even if a fast forward is possible. This registers the review branch in the commit history for future referecnce.

2. All dicsussions must be resolved before merging. When reviewers add comments and questions, it's usually for a good reason. The submitter of the MR has a responsibilty to answer all questions to the satisfaction of the reviewer - who will mark the comment as _resolved_ when the discussion has concluded.

3. Each merge request must be completed by at least one user listed in the config file. Usually this will be fellow team members.

4. Slack notification are optional and are defined in the config file under the key `slack:webhook`. This will issue a notification to a Slack channel for all Merge Request events. Slack webhooks can be created here <https://my.slack.com/services/new/incoming-webhook> for a particlar channel - add the generated URL to the config.

## Installation

Download source

`git clone git@gitlab.com:framestore/sysdev/dev/whall/fs-gitlab.git`

### Virtualenv

Install `virtualenv` using pip3 and build an isolated environment

```bash
pip3 install --upgrade pip
pip3 install virtualenv --user

mkdir ~/venv # or somewhere 
virtualenv ~/venv/fs-gitlab
source ~/venv/fs-gitlab/bin/activate
pip3 install .
```

### Local install

Performs a pip3 user install, under $HOME/.local so you will need to modify your PATH environment variable

```bash
pip3 install --user .
export PATH=$HOME/.local/bin:$PATH
```

## Config file

You *must* create a config file when uing the `create` command, default is `config.yml`. This contains details about the group members who will be required to approve any merge request.

```yaml
users:
  - user.1@framestore.com
  - user.2@framestore.com
  - user.3@framestore.com
  
slack:
  webhook: https://hooks.slack.com/services/blah/blah/blah
```

## Usage Example

Migrate from `git:/mnt/scm/git/sysdev/farm/myProject.git` to `https://gitlab.com/Framestore/sysdev/farm/my-project.git`

Here we have `myProject` in the `sysdev/farm` subdirectory. We will create the same structure in Gitlab but rename the project to `my-projcect` and perform the 3 commands explicitly.

We have -

* created the`sysdev` and `sysdev/farm` groups on Gitlab
* a personal access token in Gitlab
* exported the access token as `GITLAB_PRIVATE_TOKEN` environment variable
* access to git over SSH

```bash
fs-gitlab create sysdev/farm/fq
fs-gitlab mirror git:/scm/sysdev/farm/fq.git sysdev/farm/fq
fs-gitlab lock git:/scm/sysdev/farm/fq.git sysdev/farm/fq
```

This can be done as a single command

`fs-gitlab migrate git:/scm/sysdev/farm/fq.git sysdev/farm/fq`
