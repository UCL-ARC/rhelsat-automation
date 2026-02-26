# RH Satellite Automation

A Python script for performing specific operations in RH Satellite:

* publishing a new version of a content view
* promoting a content view version to a lifecycle environment


## Why not using `hammer` instead?

While the `hammer` CLI tool can perform these operations (and many more),
invoking it with the correct set of arguments usually requires multiple runs
of `hammer` to retrieve IDs etc.  As `hammer` runs quite slow and it doesn't
support structured output that would be easy to parse, this more integrated
script that talks directly to the Katello API was developed.


## Preparation

First, set up the Python environment:
```
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt
```

To set up the configuration file, copy `config.ini.sample` to `config.ini`
and edit it. The required settings are self-explanatory. As the file will
contain your Satellite password or personal access token, care must be taken
to keep its contents confidential, e.g. by using restrictive file permissions.


## Operation

To view the integrated usage information, run
```
./rhelsat-automate.py --help
```

The script takes a mandatory `command` argument, and to get further help for a given
command, run
```
./rhelsat-automate.py <command> --help
```

### Publishing a Content View

To publish a new version of a content view, you need provide its *label* (which
you can get from the web UI), e.g. to publish a new version of the content view
labelled `ARC_HPC_RHEL9`, run
```
./rhelsat-automate.py publish ARC_HPC_RHEL9
```

Note that this only triggers the publication process inside Satellite, but by
default doesn't wait until this process is completed. Use the `--wait` option
to make the script wait for completion.

By default, the new version number is obtained by taking the latest version of
this content view and incrementing the minor version number, e.g. if the latest
version is `4.1` the new version will be `4.2`. You can override the version
number with the `--version` option.

As a sanity check, the script first checks the sync status of all the repositories
that contribute to the content view.
- If none of the repositories were synced after the latest publication of the
  content view, then no new version will be published, as there would be no changes
  to the content.
- If some of the repositories show an unsuccessful sync status, then no new version
  will be published, as presumably you'll want to fix the sync issue first.
  (Repositories without a sync plan are ignored for this check.)

To force publication of the content view anyway, use the `--force` option.


### Promoting a Content View

*Author's note: I assume that a lifecycle environment contains only a single content view.
While technically multiple content views can be promoted to the same lifecycle
environment, I have not found a use for this feature, as long as hosts can only
receive content from a single content view anyway. (This might change with Satellite
server 6.17+.)*

To promote a content view version to a lifecycle environment, you need to provide
its *label* (which is likely the same as its name), and the script will determine
which content view is associated with this environment and then by default promote
the *latest* version of this content view to the lifecycle environment. Example:
```
./rhelsat-automate.py promote hpc-rhel9-dev
```
If you want to promote a different version of the content view, e.g. for rolling
back, use the `--version` option.

Note that this only triggers the promotion process inside Satellite, but by
default doesn't wait until this process is completed. Use the `--wait` option
to make the script wait for completion.

By default Satellite enforces that promotion respects the *lifecycle environment path*,
e.g. that a content view version is first promoted to a `test` environment before it
can be promoted to a `prod` environment. To override this, use the `--force` option.

