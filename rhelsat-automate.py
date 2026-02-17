#!/usr/bin/env python3
# vim: set et sw=4 ts=4 :

import configparser
import argparse
import logging
import sys
import json
import requests
import dateutil
import concurrent.futures as confut
from time import sleep
from datetime import datetime
from dataclasses import dataclass
from typing import Union


def process_args():
    common = argparse.ArgumentParser(add_help=False)
    common_group = common.add_argument_group("common options")
    common_group.add_argument(
        '-c', '--config',
        default='config.ini',
        help='path to config file (INI format)')
    common_group.add_argument(
        '-t', '--threads',
        type=int, default=10,
        help='number of concurrent requests (default: 10)')
    common_group.add_argument(
        '-f', '--force',
        action='store_true',
        help='force the operation')
    common_group.add_argument(
        '-w', '--wait',
        action='store_true',
        help='wait until the action is completed')
    common_group.add_argument(
        '--log-level',
        default='INFO',
        help='logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)')

    parser = argparse.ArgumentParser(
        description='Automate operations in RedHat Satellite.',
        parents=[common])
    subparsers = parser.add_subparsers(
        title='commands',
        dest='command',
        metavar='{publish,promote}',
        required=True)

    p_publish = subparsers.add_parser(
        'publish',
        parents=[common],
        help='publish a content view',
        description='Publish a Content View.')
    p_publish.add_argument(
        'content_view',
        help='label of the content view')
    p_publish.add_argument(
        '-v', '--version',
        dest='cv_version', default=None,
        help='override new content view version number (major.minor)')

    p_promote = subparsers.add_parser(
        'promote',
        parents=[common],
        help='promote a content view to a lifecycle environment',
        description='Promote a content view to a lifecycle environment.')
    p_promote.add_argument(
        'environment',
        help='label of the lifecycle environment')
    p_promote.add_argument(
        '-v', '--version',
        dest='cv_version', default=None,
        help='promote this version (major.minor) of the content view instead of the latest')

    args = parser.parse_args()
    return args


@dataclass
class KatelloServer:
    url: str
    org: str
    username: str
    password: str
    org_id: Union(int,None) = None

    def get(self, endpoint):
        cred = (self.username, self.password)
        url = f"{self.url}/katello/api{endpoint}"
        resp = requests.get(url, auth=cred)
        resp.raise_for_status()
        return resp.json()

    def post(self, endpoint, payload):
        cred = (self.username, self.password)
        url = f"{self.url}/katello/api{endpoint}"
        resp = requests.post(url, auth=cred, json=payload)
        resp.raise_for_status()
        return resp.json()

    def set_org_id(self):
        response = self.get(f'/organizations?search={self.org}')
        for result in response['results']:
            if result['label'] == self.org:
                self.org_id = result['id']
                return True
        return False

    def get_content_view(self, cv_label):
        response = self.get(f'/organizations/{self.org_id}/content_views?search={cv_label}')
        for result in response['results']:
            if result['label'] == cv_label:
                return result
        return None

    def get_lifecycle_environment(self, le_label):
        response = self.get(f'/organizations/{self.org_id}/environments?search={le_label}')
        for result in response['results']:
            if result['label'] == le_label:
                return result
        return None

    def get_cv_repos(self, cv, nthread=10):
        def get_repo(rid):
            repo = self.get(f'/repositories/{rid}')
            return repo
        repo_ids = cv['repository_ids']
        repos = []
        with confut.ThreadPoolExecutor(max_workers=nthread) as exc:
            futures = [ exc.submit(get_repo, rid) for rid in repo_ids ]
            for fut in confut.as_completed(futures):
                repos.append(fut.result())
        return repos

    def wait_for_cvv(self, cvv_id, check_action, poll_interval=20, max_unexpected=3):
        nunexpected = 0
        while True:
            response = self.get(f'/content_view_versions/{cvv_id}')
            last_event = response['last_event']
            action = last_event['action']
            if action != check_action:
                logging.error(f'last event action is "{action}", expected "{check_action}"')
                sys.exit(2)
            status = last_event['status']
            if status == 'successful':
                logging.info(f'  {action} completed')
                break
            elif status == 'in progress':
                progress = last_event['task']['progress']
                logging.info(f'  {action} progress {100*progress}%')
                sleep(poll_interval)
            else:
                nunexpected += 1
                if nunexpected <= max_unexpected:
                    logging.warning(f'unexpected status "{status}", will retry')
                    sleep(poll_interval)
                else:
                    logging.error(f'unexpected status "{status}" persists, giving up')
                    sys.exit(2)
        return


def init_logger(level='INFO'):
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=getattr(logging, level.upper()))


def load_config(filename):
    cfg = configparser.ConfigParser()
    try:
        with open(filename) as fd:
            cfg.read_file(fd)
    except Exception as err:
        logging.critical(f"failed to load configuration from '{filename}': {err}")
        sys.exit(9)
    return cfg


def parse_date(dtstr):
    # insert rant about Python's builtin datetime unwilling to parse timezones
    dt = dateutil.parser.parse(dtstr)
    return dt


def run_promote(le_label, ks, args):
    le = ks.get_lifecycle_environment(le_label)
    if not le:
        logging.error(f'cannot find lifecycle environment "{le_label}"')
        sys.exit(8)
    le_id = le['id']
    logging.info(f'found lifecycle environment "{le_label}" with id {le_id}')
    le_cvs = le['content_views']
    if len(le_cvs) == 0:
        logging.error(f'no content view associed with LE "{le_label}"')
        sys.exit(8)
    elif len(le_cvs) > 1:
        logging.error('multiple content views for one LE not implemented')
        sys.exit(8)

    le_cv = le_cvs[0]
    cv_id = le_cv['id']
    cv = ks.get(f'/content_views/{cv_id}')
    if not cv:
        logging.error(f'cannot find content view id {cv_id}')
        return 1
    cv_label = cv["label"]
    cv_version = cv["latest_version"]
    logging.info(f'associated content view id {cv_id} is "{cv_label}"')
    logging.info(f'  latest version: {cv_version}')
    logging.info(f'  last published: {cv["last_published"]}')

    if args.cv_version:
        logging.info(f'  promoting version: {args.cv_version}')
        cvv_id = None
        for v in cv['versions']:
            if v['version'] == args.cv_version:
                cvv_id = v['id']
                break
        if not cvv_id:
            logging.error('cannot find content view version {args.cv_version}')
            return 1
    else:
        cvv_id = cv['latest_version_id']
    cvv = ks.get(f'/content_view_versions/{cvv_id}')
    cv_version = cvv['version']
    if not cvv:
        logging.error(f'cannot find content view version id {cvv_id}')
        return 1
    cvv_envs = cvv['environments']
    cvv_env_ids = [ env['id'] for env in cvv_envs ]
    if le_id in cvv_env_ids:
        logging.warning(f'content view "{cv_label}" version {cv_version} already promoted to this LE')
        return 0

    payload = {
        'environment_ids': [le_id],
        'force': args.force,
        }
    logging.info(f'  promoting content view "{cv_label}" version {cv_version}...')
    try:
        output = ks.post(f'/content_view_versions/{cvv_id}/promote', payload)
        if args.wait:
            ks.wait_for_cvv(cvv_id, 'promotion')
        return 0
    except requests.exceptions.HTTPError as err:
        resp = err.response
        resp_obj = resp.json()
        logging.error(f'status {resp.status_code}: {resp_obj["displayMessage"]}')
        return 1


def run_publish(cv_label, ks, args):
    cv = ks.get_content_view(cv_label)
    if not cv:
        logging.error(f'cannot find content view "{cv_label}"')
        return 1
    cv_id = cv['id']
    logging.info(f'found content view "{cv_label}" with id {cv_id}')
    logging.info(f'  latest version: {cv["latest_version"]}')
    logging.info(f'  last published: {cv["last_published"]}')
    dt_cv_last_published = parse_date(cv["last_published"])

    nrepo = len(cv["repository_ids"])
    logging.info(f'fetching data for {nrepo} repositories...')
    repos = ks.get_cv_repos(cv, nthread=args.threads)
    nnosyncplan = 0
    nsyncsuccess = 0
    dt_latest_sync = None
    for repo in repos:
        name = repo['name']
        prod = repo['product']
        if not prod['sync_plan']:
            logging.debug(f'  "{name}" has no sync plan')
            nnosyncplan += 1
            continue
        sync = repo['last_sync']
        if not sync:
            logging.warning(f'  "{name}" has never been synced?')
            continue
        if sync['state']!='stopped' or sync['result']!='success':
            logging.warning(f'  "{name}" sync is {sync["state"]}')
            continue
        sync_ended = sync['ended_at']
        logging.debug(f'  "{name}" synced at {sync_ended}')
        nsyncsuccess += 1
        dt_sync_ended = parse_date(sync_ended)
        if (dt_latest_sync is None) or (dt_sync_ended > dt_latest_sync):
            dt_latest_sync = dt_sync_ended
    logging.info(f'  {nsyncsuccess} repos synced, {nnosyncplan} without sync plan')
    logging.info(f'  latest repo sync: {dt_latest_sync.strftime("%Y-%m-%d %H:%M:%S %Z")}')

    if nsyncsuccess + nnosyncplan < nrepo:
        level = logging.WARNING if args.force else logging.ERROR
        logging.log(msg='not all repos are synced', level=level)
        if not args.force:
            return 1

    if dt_latest_sync <= dt_cv_last_published:
        logging.warning('content view already published after latest repo sync')
        if not args.force:
            return 0
    
    if args.cv_version:
        major, minor = map(int, args.cv_version.split('.'))
    else:
        cvv_id = cv['latest_version_id']
        cvv = ks.get(f'/content_view_versions/{cvv_id}')
        major = cvv['major']
        minor = cvv['minor'] + 1
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    payload = {
        'description': f'auto-publish {now}',
        'major': major,
        'minor': minor,
        }
    logging.info(f'publishing content view version {major}.{minor}...')
    try:
        output = ks.post(f'/content_views/{cv_id}/publish', payload)
        if output:
            cvv_id = output['input']['content_view_version_id']
            logging.info(f'new content view version id = {cvv_id}')
            if args.wait:
                ks.wait_for_cvv(cvv_id, 'publish')
        return 0
    except requests.exceptions.HTTPError as err:
        resp = err.response
        resp_obj = resp.json()
        logging.error(f'error {resp.status_code}: {resp_obj["displayMessage"]}')
        return 1


if __name__ == '__main__':
    args = process_args()
    init_logger(args.log_level)
    cfg = load_config(args.config)
    ks  = KatelloServer(**cfg['satellite'])

    if not ks.set_org_id():
        logging.error(f'cannot find organization "{ks.org}"')
        sys.exit(8)
    logging.info(f'found organization "{ks.org}" with id {ks.org_id}')

    rc = 0
    if args.command == 'publish':
        cv_label = args.content_view
        rc = run_publish(cv_label, ks, args)
    elif args.command == 'promote':
        le_label = args.environment
        rc = run_promote(le_label, ks, args)

    if rc == 0:
        logging.info('operations completed successfully')
    else:
        logging.warning('operations completed with errors')
    sys.exit(rc)
