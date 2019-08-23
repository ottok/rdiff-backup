# How to contribute

Rdiff-backup is an open source software developed by many people over a long period of time. There is no particular company backing the development of rdiff-backup, so we rely very much on individual contributors who "scratch their itch". **All contributions are welcome!**

#### Ways to contribute:

- Testing, troubleshooting and writing good bug reports that are easy for other developers to read and act upon
- Reviewing and triaging existing bug reports and issues, helping other developers focus their efforts
- Writing documentation (e.g. the [man page](https://github.com/rdiff-backup/rdiff-backup/blob/master/rdiff-backup.1)), or updating the webpage rdiff-backup.net
- Packaging and shipping rdiff-backup in your own favorite Linux distribution or operating system
- Running tests on your favorite platforms and fixing failing tests
- Writing new tests to get test coverage up
- Fixing bug in existing features or adding new features

If you don't have anything particular in your mind but want to help out, just browse the list of issues. Both coding and non-coding tasks have been filed as issues.

## Guidelines on contributing

- Rdiff-backup is released as GNU General Public License v2.0. By contributing to this repository you agree that your work is licensed using the chosen project license.
- Before committing to a lot of writing or coding, please file an issue on Github and discuss your plans and gather feedback. Eventually it will be much easier to merge your change request if the idea and design has been agreed upon, and there will be less work for you as a contributor if you implement your idea along the correct lines to begin with.
- Please check out [existing issues](https://github.com/rdiff-backup/rdiff-backup/issues), [existing merge requests(https://github.com/rdiff-backup/rdiff-backup/pulls)] and browse the [git history](https://github.com/rdiff-backup/rdiff-backup/commits/master) to see if somebody already tried to address the thing you have are interested in. It might provide useful insight why the current state is as it is.
- Changes can be submitted using the typical Github workflow: clone this repository, make your changes, test and verify, and submit a Pull Request.
- Each change (= pull request) should focus on some topic and resist changing anything else. Keeping the scope clear also makes it easier to review the pull request. A good pull request has only one or a few commits, with each commit having a good commit subject and if needed also a body that explains the change.
- For all code changes, please remember also to include inline comments and update tests where needed.

## Branching model and pull requests

The *master* branch is always kept in a clean state. Anybody can at any time branch off from *master* and expect test suite to pass and the code and other contents to be of good quality and a reasonable foundation for them to continue development on.

Each pull request has only one author, but anybody can give feedback. The original author should be given time to address the feedback – reviewers should not do the fixes for the author, but instead let the author keep the authorship. Things can always be iterated and extended in future commits once the PR has been merged, or even in parallel if the changes are in different files or at least on different lines and do not cause merge conflicts if worked on.

If a pull requests for whatever reason is not quickly merged, should it be refreshed by [rebasing](https://git-scm.com/docs/git-rebase) it on latest upstream master.

Ideally each pull request gets some feedback within 24 hours from it having been filed, and is merged within days or a couple of weeks. Each author should facilitate quick reviews and merges by making clean and neat commits and pull requests that are quick to review and do not spiral out in long discussions.

Currently the rdiff-backup Github repository is configured so that merging a pull request is possible only if it:
- passes the CI testing
- has at least one approving review


## Coding style

This project is written in Python, and should strive to follow the official [PEP 8 coding standard](https://www.python.org/dev/peps/pep-0008/).

For other technical tips see also [DEVELOP.adoc](DEVELOP.adoc)
