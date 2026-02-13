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


@dataclass
class SatelliteConfig:
    url: str
    org: str
    username: str
    password: str
    org_id: Union(int,None) = None


def process_args():
    parser = argparse.ArgumentParser(
        description='Automate operations in RedHat Satellite.')
    parser.add_argument(
        '-c', '--config',
        default='config.ini',
        help='path to config file (INI format)')
    parser.add_argument(
        '-t', '--threads',
        type=int, default=10,
        help='number of concurrent requests')
    parser.add_argument(
        '-f', '--force',
        action='store_true',
        help='force the operation')
    parser.add_argument(
        '-w', '--wait',
        action='store_true',
        help='wait until the action is completed')
    parser.add_argument(
        '--log-level',
        default='INFO',
        help='logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)')

    subparsers = parser.add_subparsers(
        title='commands',
        dest='command',
        metavar='{publish,promote}',
        required=True)
    p_publish = subparsers.add_parser(
        'publish',
        help='publish a content view',
        description='Publish a Content View.')
    p_publish.add_argument(
        'content_view',
        help='label of the content view')
    p_promote = subparsers.add_parser(
        'promote',
        help='promote a content view to a lifecycle environment',
        description='Promote a content view to a lifecycle environment.')
    p_promote.add_argument(
        'environment',
        help='label of the lifecycle environment')
    args = parser.parse_args()
    return args


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


def day_of_year(dt):
    year = dt.year
    t0 = datetime(year, 1, 1)
    doy = (dt-t0).days
    return doy


def katello_get(endpoint, cfg):
    cred = (cfg.username, cfg.password)
    url = f"{cfg.url}/katello/api{endpoint}"
    resp = requests.get(url, auth=cred)
    resp.raise_for_status()
    return resp.json()


def katello_post(endpoint, payload, cfg):
    cred = (cfg.username, cfg.password)
    url = f"{cfg.url}/katello/api{endpoint}"
    resp = requests.post(url, auth=cred, json=payload)
    resp.raise_for_status()
    return resp.json()


def get_org_id(org, cfg):
    response = katello_get('/organizations', cfg)
    for result in response['results']:
        if result['label'] == org:
            org_id = result['id']
            return org_id
    return None


def get_content_view(cv_label, cfg):
    response = katello_get(f'/organizations/{cfg.org_id}/content_views?search={cv_label}', cfg)
    for result in response['results']:
        if result['label'] == cv_label:
            return result
    return None


def get_cv_repos(cv, cfg, nthread=10):
    def get_repo(rid):
        repo = katello_get(f'/repositories/{rid}', cfg)
        return repo
    repo_ids = cv['repository_ids']
    repos = []
    with confut.ThreadPoolExecutor(max_workers=nthread) as exc:
        futures = [ exc.submit(get_repo, rid) for rid in repo_ids ]
        for fut in confut.as_completed(futures):
            repos.append(fut.result())
    return repos


def run_promote(le_label, cfg, args):
    pass


def run_publish(cv_label, cfg, args):
    cv = get_content_view(cv_label, cfg)
    if not cv:
        logging.error(f'cannot find content view "{cv_label}"')
        sys.exit(8)
    cv_id = cv['id']
    logging.info(f'found content view "{cv_label}" with id {cv_id}')
    logging.info(f'  latest version: {cv["latest_version"]}')
    logging.info(f'  last published: {cv["last_published"]}')
    dt_cv_last_published = parse_date(cv["last_published"])

    nrepo = len(cv["repository_ids"])
    logging.info(f'fetching data for {nrepo} repositories...')
    repos = get_cv_repos(cv, cfg, nthread=args.threads)
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
            sys.exit(2)

    if dt_latest_sync <= dt_cv_last_published:
        logging.warning('content view already published after latest repo sync')
        if not args.force:
            return None
    
    now = datetime.now()
    major = now.year
    minor = day_of_year(now)
    payload = {
        'description': 'auto-publish',
        'major': major,
        'minor': minor,
        }
    logging.info(f'publishing content view version {major}.{minor}...')
    response = katello_post(f'/content_views/{cv_id}/publish', payload, cfg)
    return response


def wait_for_cvv(cvv_id, cfg, poll_interval=20, max_unexpected=3):
    nunexpected = 0
    while True:
        response = katello_get(f'/content_view_versions/{cvv_id}', cfg)
        last_event = response['last_event']
        action = last_event['action']
        if action != 'publish':
            logging.error(f'last event action is "{action}", expected "publish"')
            sys.exit(2)
        status = last_event['status']
        if status == 'successful':
            logging.info(f'publish completed')
            break
        elif status == 'in progress':
            progress = last_event['task']['progress']
            logging.info(f'publish progress {100*progress}%')
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


if __name__ == '__main__':
    args = process_args()
    init_logger(args.log_level)
    cfg = load_config(args.config)
    sat_cfg = SatelliteConfig(**cfg['satellite'])

    org = sat_cfg.org
    org_id = get_org_id(org, sat_cfg)
    if not org_id:
        logging.error(f'cannot find organizaion "{org}"')
        sys.exit(8)
    logging.info(f'found organization "{org}" with id {org_id}')
    sat_cfg.org_id = org_id

    response = None
    if args.command == 'publish':
        cv_label = args.content_view
        response = run_publish(cv_label, sat_cfg, args)
        if response:
            cvv_id = response['input']['content_view_version_id']
            logging.info(f'new content view version id = {cvv_id}')
            if args.wait:
                wait_for_cvv(cvv_id, sat_cfg)
    elif args.command == 'promote':
        le_label = args.environment
        response = run_promote(le_label, sat_cfg, args)

    logging.info('all operations complete')

