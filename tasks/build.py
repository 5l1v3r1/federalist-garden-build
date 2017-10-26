'''
Build tasks and helpers
'''

import os
import json
import shutil
import requests

from contextlib import ExitStack
from pathlib import Path
from invoke import task, call

from .common import (CLONE_DIR_PATH, SITE_BUILD_DIR,
                     WORKING_DIR, SITE_BUILD_DIR_PATH,
                     clean, logging)

LOGGER = logging.getLogger('BUILD')

# Initialize RVM
# Initialize NVM
# Install NPM deps
# Run NPM federalist script
# Run Jekyll (with custom config and BASEURL)
# Run Hugo with BASEURL
# Run 'static' mv

# TODO: Any way to persist the node version instead of
# always having to prefix with `nvm use`?

NVM_SH_PATH = Path(os.path.join(os.environ['NVM_DIR'], 'nvm.sh'))
RVM_PATH = Path('/usr/local/rvm/scripts/rvm')

PACKAGE_JSON_PATH = Path(os.path.join(CLONE_DIR_PATH, 'package.json'))
NVMRC_PATH = Path(os.path.join(CLONE_DIR_PATH, '.nvmrc'))
RUBY_VERSION_PATH = Path(os.path.join(CLONE_DIR_PATH), '.ruby-version')
GEMFILE_PATH = Path(os.path.join(CLONE_DIR_PATH), 'Gemfile')
JEKYLL_CONF_YML_PATH = os.path.join(CLONE_DIR_PATH, '_config.yml')


def has_federalist_script():
    '''
    Checks for existence of the "federalist" script in the
    cloned repo's package.json.
    '''

    if PACKAGE_JSON_PATH.is_file():
        with open(PACKAGE_JSON_PATH) as json_file:
            package_json = json.load(json_file)
            return 'federalist' in package_json.get('scripts', {})

    return False


# TODO: Do we need to activate nvm before running jekyll and hugo?
@task
def setup_node(ctx):
    '''
    If package.json is in the cloned repo, then install production
    node dependencies and run the the federlist script if present.

    Also uses the node version specified in the cloned repo's .nvmrc
    file if it is present.
    '''

    with ctx.cd(CLONE_DIR_PATH):
        with ctx.prefix(f'source {NVM_SH_PATH}'):
            if NVMRC_PATH.is_file():
                LOGGER.info('Using node version specified in .nvmrc')
                ctx.run('nvm install', env={})

            node_ver_res = ctx.run('node --version', env={})
            LOGGER.info(f'Node version: {node_ver_res.stdout}')

            npm_ver_res = ctx.run('npm --version', env={})
            LOGGER.info(f'NPM version: {npm_ver_res.stdout}')

            if PACKAGE_JSON_PATH.is_file():
                with ctx.prefix('nvm use'):
                    LOGGER.info('Installing production dependencies in package.json')
                    ctx.run('npm install --production', env={})

def node_context(ctx, *more_contexts):
    '''
    Creates an ExitStack context manager that includes the
    pyinvoke ctx with nvm prefixes.

    Additionally supplied more_contexts (like `ctx.cd(...)`) will be
    included in the returned ExitStack.
    '''
    contexts = [
        ctx.prefix(f'source {NVM_SH_PATH}'),
    ]

    # Only use `nvm use` if `.nvmrc` exists.
    # The default node version will be used if `.nvmrc` is not present.
    if NVMRC_PATH.is_file():
        contexts.append(ctx.prefix('nvm use'))

    contexts += more_contexts
    stack = ExitStack()
    for cm in contexts:
        stack.enter_context(cm)
    return stack

def build_env(branch, owner, repository, site_prefix, base_url):
    return {
        'BRANCH': branch,
        'OWNER': owner,
        'REPOSITORY': repository,
        'SITE_PREFIX': site_prefix,
        'BASEURL': base_url,
    }


@task(pre=[setup_node])
def run_federalist_script(ctx):
    if PACKAGE_JSON_PATH.is_file() and has_federalist_script():
        with node_context(ctx, ctx.cd(CLONE_DIR_PATH)):
            LOGGER.info('Running federalist build script in package.json')
            ctx.run('npm run federalist', env={})

@task
def setup_ruby(ctx):
    with ctx.prefix(f'source {RVM_PATH}'):
        if RUBY_VERSION_PATH.is_file():
            ruby_version = ''
            with open(RUBY_VERSION_PATH, 'r') as f:
                ruby_version = f.readline().strip()
            if ruby_version:
                LOGGER.info('Using ruby version in .ruby-version')
                ctx.run('rvm install {ruby_version}')

        ruby_ver_res = ctx.run('ruby -v')
        LOGGER.info(f'Ruby version: {ruby_ver_res.stdout}')



@task(pre=[run_federalist_script, setup_ruby])
def build_jekyll(ctx, branch, owner, repository, site_prefix, config='', base_url=''):
    # Add baseurl, branch, and the custom config to _config.yml
    with open(JEKYLL_CONF_YML_PATH, 'a') as f:
        f.writelines([
            '\n'
            f'baseurl: {base_url}\n',
            f'branch: {branch}\n',
            config,
        ])

    source_rvm = ctx.prefix(f'source {RVM_PATH}')
    with node_context(ctx, source_rvm, ctx.cd(CLONE_DIR_PATH)):
        use_bundle = False
        jekyll_cmd = 'jekyll'

        if GEMFILE_PATH.is_file():
            LOGGER.info('Setting up bundler')
            ctx.run('gem install bundler', env={})
            LOGGER.info('Installing dependencies in Gemfile')
            ctx.run('bundle install', env={})
            jekyll_cmd = 'bundle exec ' + jekyll_cmd

        else:
            LOGGER.info('Installing Jekyll')
            ctx.run('gem install jekyll', env={})

        jekyll_vers_res = ctx.run(f'{jekyll_cmd} -v', env={})
        LOGGER.info(f'Building using Jekyll version: {jekyll_vers_res.stdout}')

        ctx.run(
            f'{jekyll_cmd} build --destination {SITE_BUILD_DIR}',
            env=build_env(branch, owner, repository, site_prefix, base_url)
        )

@task
def install_hugo(ctx, version='0.23'):
    LOGGER.info(f'Downloading and installing hugo version {version}')
    dl_url = (f'https://github.com/gohugoio/hugo/releases/download/'
              f'v{version}/hugo_{version}_Linux-64bit.deb')
    response = requests.get(dl_url)
    hugo_deb = os.path.join(WORKING_DIR, 'hugo.deb')
    with open(hugo_deb, 'wb') as fd:
        for chunk in response.iter_content(chunk_size=128):
            fd.write(chunk)
    ctx.run(f'dpkg -i {hugo_deb}', env={})


@task(pre=[run_federalist_script])
def build_hugo(ctx, branch, owner, repository, site_prefix, base_url='', hugo_version='0.23'):
    install_hugo(ctx, hugo_version)
    hugo_vers_res = ctx.run('hugo version', env={})
    LOGGER.info(f'hugo version: {hugo_vers_res.stdout}')
    LOGGER.info('Building site with hugo')
    with node_context(ctx, ctx.cd(CLONE_DIR_PATH)):
        hugo_args = f'--source . --destination {SITE_BUILD_DIR}'
        if base_url:
            hugo_args += f' --baseUrl {base_url}'
        ctx.run(
            f'hugo {hugo_args}',
            env=build_env(branch, owner, repository, site_prefix, base_url)
        )

@task(pre=[
    run_federalist_script,
    # Remove cloned repo's .git directory
    call(clean, which=os.path.join(CLONE_DIR_PATH, '.git')),
])
def build_static(ctx):
    '''Moves all files from CLONE_DIR into SITE_BUILD_DIR'''
    LOGGER.info(f'Moving files to {SITE_BUILD_DIR}')
    os.makedirs(SITE_BUILD_DIR_PATH)
    files = os.listdir(CLONE_DIR_PATH)
    for file in files:
        # don't move the _site dir into itself
        if file is not SITE_BUILD_DIR:
            shutil.move(os.path.join(CLONE_DIR_PATH, file),
                        SITE_BUILD_DIR_PATH)
