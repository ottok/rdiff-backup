# README for Debian package maintainers and contributors

This Debian packaging source code in directory `debian/` is maintained on branch
`debian/latest` (naming following DEP-14) as part of a fork of the upstream
repository. This structure is compatible with git-buildpackage and is
preconfigured with `debian/gbp.conf` so the git-buildpackage commands don't need
extra parameters most of the time.

To understand what each git-buildpackage command in this README exactly does,
run them with `--verbose` and read the respective man pages for details.


## Getting the Debian packaging source code

To get the Debian packaging source code and have the upstream remote alongside
it, simply run:

    gbp clone vcsgit:rdiff-backup --add-upstream-vcs

Using the `vcsgit:`prefix will automatically resolve the git repository
location, which for most packages is on salsa.debian.org. To build the package
one needs all three Debian branches (`debian/latest`, `upstream/latest`and
`pristine-tar`). Using `gbp clone` and `gbp pull` ensures all three branches are
automatically fetched.

The command above also automatically adds the upstream repository as an extra
remote called `upstreamvcs`, and fetches the latest upstream `master` branch
commits and tags. The upstream development branch is not a requirement to build
the Debian package, but is recommended for making collaboration with upstream
easy.

On older git-buildpackage versions the `--add-upstream-vcs` might not yet work,
but you can achieve the same with manually running:

    git remote add -t master -f upstreamvcs https://github.com/rdiff-backup/rdiff-backup.git

The repository structure and use of `gbp pq` makes it easy to cherry-pick
commits between upstream and downstream Debian, ensuring improvements downstream
in Debian and upstream in the original project are shared frictionlessly.


## Updating an existing local git repository

If you have an existing local repository created in this way, you can update it
by simply running:

    gbp pull --verbose

To also get the upstream remote updated run:

    git pull --verbose --all

The recommended tool to inspect what branches and tags you have and what their
state is on various remotes is:

    gitk --all &


## Contributing to the Debian packaging

First clone the Debian packaging repository using git-buildpackage as described
above. Then open https://salsa.debian.org/python-team/packages/rdiff-backup and press
"Fork". This is needed for Salsa to understand that your repository has the same
origin. In your fork, note the git SSH address, e.g.
`git@salsa.debian.org:otto/rdiff-backup.git`, and add it as new remote (replace
'otto' with your own Salsa username):

    git remote add otto git@salsa.debian.org:otto/rdiff-backup.git

Do your code changes, commit and push to your repository:

    git checkout -b bugfix/123456-fix-something
    git commit # or `git citool`
    git push --set-upstream otto bugfix/123456-fix-something

If made further modifications, and need to update your submission, run:

    git commit -a --amend # or `git citool --amend`
    git push -fv

Finally open a Merge Request on salsa.debian.org. If your submission is high
enough quality, the maintainer is likely to approve it and include your
improvement in the revision of the Debian package. The link to open an MR will
automatically display on the command-line after each `git push` run.

There is no need to update the `debian/changelog` file in the commit. It will be
done automatically by the maintainer before next upload to Debian. There is also
no need to submit multiple Merge Requests targeting different branches with the
same change. Just submit the change for the `debian/latest` branch, and the
maintainer will cherry-pick it to other branches as needed.

The Debian packaging repository will only accept changes in the `debian/`
subdirectory. Any fix for upstream code should be contributed directly to
upstream.


## Adding a patch to the Debian packaging

The Debian packaging consists of the pristine upstream source code combined with
the `debian/` subdirectory where all Debian packaging code resides. As the
upstream source code needs to stay untouched, so any modification of upstream
code must be done as a patch in the `debian/patches/` subdirectory, which is
then applied on upstream source code at build-time.

Instead of manually fiddling with patch files, the recommended way to update
them is using `gbp pq`. Start by deleting any remnants of an old temporary patch
queue branch, and import latest `debian/patches` contents and switch to a
temporary patches-applied branch by running:

    gbp pq drop && gbp pq import
    # Make changes, build, test
    git commit -a --amend # or `git citool --amend`

If your terminal prompt shows the git branch, you will see it change from e.g.
`debian/latest` to `patch-queue/debian/latest`. You can do whatever modification
you want on _this patches-applied branch_, such as add commits, cherry-pick,
rebase or whatever. Just keep in mind that for each commit you will eventually
have a file in `debian/patches` in the final Debian packaging sources. There is
no need to switch back to the "real" branch as you can build and test that
everything works on this same branch effortlessly.

Only when finally done, convert the patch-queue commits back to a correctly
formatted patch file by running:

    gbp pq export
    git commit -a --amend # or `git citool --amend`

If your terminal prompt shows the git branch, you will see it have changed back
to `debian/latest`. The updates you committed in `debian/patches/...` can be
sent as a Merge Request on Salsa to the Debian package. The commit done on the
`patch-queue/debian/latest` can be sent upstream as-is.

Once done, discard the temporary branch with:

    gbp pq drop


## Contributing upstream

This Debian packaging repository and the upstream git repository can happily
co-exist as separate branches in the same git repository. To contribute
upstream, start by opening the upstream project GitHub page, press "Fork" and
add it as yet another git remote to this repository just like in the section
above.

Make git commits, or cherry-pick a commit that is already on a `gbp pq` branch,
push them to your GitHub fork and open a Pull Request on the upstream
repository.


## Importing a new upstream release

To check for new upstream releases run:

    gbp pq drop && gbp pq import
    # Note branch name where the temporary patch-queue will be waiting
    git fetch --verbose upstream master
    # Note latest tag, e.g. 5.6
    gbp import-orig --uscan
    gbp dch --distribution=UNRELEASED \
      --commit --commit-msg="Update changelog and refresh patches after %(upstreamversion)s import" \
      -- debian
    gbp pq rebase # or manually switch to patch-queue branch and use regular `git rebase`
    gbp pq export
    git commit -a --amend # or `git citool --amend`

If the upstream version is not detected correctly, you can pass to `gbp dch` the
extra parameter `--new-version=5.6`.

If rebasing the patch queue causes merge conflicts, run `git mergetool` to
visually resolve them. You can also browse the upstream changes on a particular
file easily with `gitk path/to/file`.

When adding DEP3 metadata fields to patches, put them as the first lines in the
git commit message on the `pq` branch, or alternatively edit the
`debian/patches/*` files directly. Ensure the first three lines are always
`From`, `Date` and `Subject` just like in `git am` managed patches.

Remember that if you did more than just refreshed patches, you should save those
changes in separate git commits. Remember to build the package, run autopkgtests
and conduct other appropriate testing. Easiest way to do it is with:

    debcraft validate
    debcraft build
    debcraft test

You can also do manual testing and run `apt install <package>` in a `debcraft
shell` session. Rinse and repeat until the Debian packaging has been properly
updated in response to the changes in the new upstream version.

After testing enough locally, push to your fork and open Merge Request on Salsa
for review (replace 'otto' with your own Salsa username):

    gbp push --verbose otto

Note that git-buildpackage will automatically push all three branches
(`debian/latest`, `upstream/latest` and `pristine-tar`) and upstream tags to
your fork so it can run the CI. However, merging the MR will only merge one
branch (`debian/latest`) so the Debian maintainer will need to push the other
branches to the Debian packaging git repository manually with `git push`. It is
not a problem though, as the upstream import is mechanical for the
`upstream/latest` and `pristine-tar` branches. Only the `debian/latest` branch
has changes that warrant a review and potentially new revisions.


## Uploading a new release

**You need to be a Debian Developer to be able to upload to Debian.**

Before the upload, remember to ensure that the `debian/changelog` is
up-to-date:

    gbp dch --release --commit

Create a source package with your preferred tool. In Debcraft, one would issue:

    debcraft release

Do the final checks and sign and upload with:

    debsign *.changes
    dput ftp-master *.changes

After upload remember to monitor your email for the acknowledgement email from
Debian systems. Once the upload has been accepted, remember to run:

    gbp tag --verbose
    gbp push --verbose
