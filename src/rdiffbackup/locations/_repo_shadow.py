# Copyright 2002, 2003 Ben Escoto
#
# This file is part of rdiff-backup.
#
# rdiff-backup is free software; you can redistribute it and/or modify
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 2 of the License, or (at your
# option) any later version.
#
# rdiff-backup is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with rdiff-backup; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301, USA

"""
A shadow repository is called like this because like a shadow it does
what the local representation of the repository is telling it to do, but
it has no real life of itself, i.e. it has only class methods and can't
be instantiated.
"""

import errno
import io
import os
import tempfile
from rdiff_backup import (
    C, Globals, Hardlink, hash, increment, iterfile, log, longname, metadata,
    Rdiff, robust, rorpiter, rpath, selection, statistics, Time,
)

# ### COPIED FROM BACKUP ####


# @API(ShadowRepo, 201)
class ShadowRepo:
    """
    Shadow repository for the local repository representation
    """

    # If selection command line arguments given, use Select here
    _select = None
    # This will be set to the time of the current mirror
    _mirror_time = None
    # This will be set to the exact time to restore to (not restore_to_time)
    _restore_time = None

    @classmethod
    def set_rorp_cache(cls, baserp, source_iter, use_increment):
        """
        Initialize cls.CCPP, the destination rorp cache

        use_increment should be true if we are mirror+incrementing,
        false if we are just mirroring.
        """
        dest_iter = cls._get_dest_select(baserp, use_increment)
        collated = rorpiter.Collate2Iters(source_iter, dest_iter)
        cls.CCPP = _CacheCollatedPostProcess(
            collated, Globals.pipeline_max_length * 4, baserp)
        # pipeline len adds some leeway over just*3 (to and from and back)

    @classmethod
    def get_sigs(cls, dest_base_rpath):
        """
        Yield signatures of any changed destination files
        """
        flush_threshold = Globals.pipeline_max_length - 2
        num_rorps_seen = 0
        for src_rorp, dest_rorp in cls.CCPP:
            # If we are backing up across a pipe, we must flush the pipeline
            # every so often so it doesn't get congested on destination end.
            if (Globals.backup_reader is not Globals.backup_writer):
                num_rorps_seen += 1
                if (num_rorps_seen > flush_threshold):
                    num_rorps_seen = 0
                    yield iterfile.MiscIterFlushRepeat
            if not (src_rorp and dest_rorp and src_rorp == dest_rorp
                    and (not Globals.preserve_hardlinks
                         or Hardlink.rorp_eq(src_rorp, dest_rorp))):

                index = src_rorp and src_rorp.index or dest_rorp.index
                sig = cls._get_one_sig(dest_base_rpath, index, src_rorp,
                                       dest_rorp)
                if sig:
                    cls.CCPP.flag_changed(index)
                    yield sig

    @classmethod
    def patch(cls, dest_rpath, source_diffiter, start_index=()):
        """Patch dest_rpath with an rorpiter of diffs"""
        ITR = rorpiter.IterTreeReducer(_RepoPatchITRB, [dest_rpath, cls.CCPP])
        for diff in rorpiter.FillInIter(source_diffiter, dest_rpath):
            log.Log("Processing file {cf}".format(cf=diff), log.INFO)
            ITR(diff.index, diff)
        ITR.finish_processing()
        cls.CCPP.close()
        dest_rpath.setdata()

    @classmethod
    def patch_and_increment(cls, dest_rpath, source_diffiter, inc_rpath):
        """Patch dest_rpath with rorpiter of diffs and write increments"""
        ITR = rorpiter.IterTreeReducer(_RepoIncrementITRB,
                                       [dest_rpath, inc_rpath, cls.CCPP])
        for diff in rorpiter.FillInIter(source_diffiter, dest_rpath):
            log.Log("Processing changed file {cf}".format(cf=diff), log.INFO)
            ITR(diff.index, diff)
        ITR.finish_processing()
        cls.CCPP.close()
        dest_rpath.setdata()

    @classmethod
    def _get_dest_select(cls, rpath, use_metadata=True):
        """

        Return destination select rorpath iterator

        If metadata file doesn't exist, select all files on
        destination except rdiff-backup-data directory.

        """

        def get_iter_from_fs():
            """Get the combined iterator from the filesystem"""
            sel = selection.Select(rpath)
            sel.parse_rbdir_exclude()
            return sel.set_iter()

        metadata.SetManager()
        if use_metadata:
            rorp_iter = metadata.ManagerObj.GetAtTime(Time.prevtime)
            if rorp_iter:
                return rorp_iter
        return get_iter_from_fs()

    @classmethod
    def _get_one_sig(cls, dest_base_rpath, index, src_rorp, dest_rorp):
        """Return a signature given source and destination rorps"""
        if (Globals.preserve_hardlinks and src_rorp
                and Hardlink.is_linked(src_rorp)):
            dest_sig = rpath.RORPath(index)
            dest_sig.flaglinked(Hardlink.get_link_index(src_rorp))
        elif dest_rorp:
            dest_sig = dest_rorp.getRORPath()
            if dest_rorp.isreg():
                dest_rp = longname.get_mirror_rp(dest_base_rpath, dest_rorp)
                sig_fp = cls._get_one_sig_fp(dest_rp)
                if sig_fp is None:
                    return None
                dest_sig.setfile(sig_fp)
        else:
            dest_sig = rpath.RORPath(index)
        return dest_sig

    @classmethod
    def _get_one_sig_fp(cls, dest_rp):
        """Return a signature fp of given index, corresponding to reg file"""
        if not dest_rp.isreg():
            log.ErrorLog.write_if_open(
                "UpdateError", dest_rp,
                "File changed from regular file before signature")
            return None
        if (Globals.process_uid != 0 and not dest_rp.readable()
                and dest_rp.isowner()):
            # This branch can happen with root source and non-root
            # destination.  Permissions are changed permanently, which
            # should propagate to the diffs
            dest_rp.chmod(0o400 | dest_rp.getperms())
        try:
            return Rdiff.get_signature(dest_rp)
        except OSError as e:
            if (e.errno == errno.EPERM or e.errno == errno.EACCES):
                try:
                    # Try chmod'ing anyway -- This can work on NFS and AFS
                    # depending on the setup. We keep the if() statement
                    # above for performance reasons.
                    dest_rp.chmod(0o400 | dest_rp.getperms())
                    return Rdiff.get_signature(dest_rp)
                except OSError:
                    log.Log.FatalError(
                        "Could not open file {fi} for reading. Check "
                        "permissions on file.".format(fi=dest_rp))
            else:
                raise

    @classmethod
    def touch_current_mirror(cls, data_dir, current_time_str):
        """
        Make a file like current_mirror.<datetime>.data to record time

        When doing an incremental backup, this should happen before any
        other writes, and the file should be removed after all writes.
        That way we can tell whether the previous session aborted if there
        are two current_mirror files.

        When doing the initial full backup, the file can be created after
        everything else is in place.
        """
        mirrorrp = data_dir.append(b'.'.join(
            (b"current_mirror", os.fsencode(current_time_str), b"data")))
        log.Log("Writing mirror marker {mm}".format(mm=mirrorrp), log.INFO)
        try:
            pid = os.getpid()
        except BaseException:
            pid = "NA"
        mirrorrp.write_string("PID {pp}\n".format(pp=pid))
        mirrorrp.fsync_with_dir()

    @classmethod
    def remove_current_mirror(cls, data_dir):
        """
        Remove the older of the current_mirror files.

        Use at end of session
        """
        curmir_incs = data_dir.append(b"current_mirror").get_incfiles_list()
        assert len(curmir_incs) == 2, (
            "There must be two current mirrors not '{ilen}'.".format(
                ilen=len(curmir_incs)))
        if curmir_incs[0].getinctime() < curmir_incs[1].getinctime():
            older_inc = curmir_incs[0]
        else:
            older_inc = curmir_incs[1]
        if Globals.do_fsync:
            # Make sure everything is written before current_mirror is removed
            C.sync()
        older_inc.delete()

    @classmethod
    def close_statistics(cls, end_time):
        """
        Close out the tracking of the backup statistics.

        Moved to run at this point so that only the clock of the system on which
        rdiff-backup is run is used (set by passing in time.time() from that
        system). Use at end of session.
        """
        if Globals.print_statistics:
            statistics.print_active_stats(end_time)
        if Globals.file_statistics:
            statistics.FileStats.close()
        statistics.write_active_statfileobj(end_time)

# ### COPIED FROM RESTORE ####

    @classmethod
    def initialize_restore(cls, data_dir, restore_to_time):
        """Set class variable _restore_time on mirror conn"""
        cls._data_dir = data_dir
        cls._restore_time = cls._get_rest_time(restore_to_time)
        # it's a bit ugly to set the values to another class, but less than
        # the other way around as it used to be
        RestoreFile.initialize(cls._restore_time, cls.get_mirror_time())

    @classmethod
    def get_mirror_time(cls):
        """
        Return time (in seconds) of latest mirror

        Cache the mirror time for performance reasons
        """
        # this function is only used internally (for now) but it might change
        # hence it looks like an external function potentially called remotely
        if not cls._mirror_time:
            cur_mirror_incs = cls._data_dir.append(
                b"current_mirror").get_incfiles_list()
            if not cur_mirror_incs:
                log.Log.FatalError("Could not get time of current mirror")
            elif len(cur_mirror_incs) > 1:
                log.Log("Two different times for current mirror found",
                        log.WARNING)
            cls._mirror_time = cur_mirror_incs[0].getinctime()
        return cls._mirror_time

    @classmethod
    def get_increment_times(cls, rp=None):
        """Return list of times of backups, including current mirror

        Take the total list of times from the increments.<time>.dir
        file and the mirror_metadata file.  Sorted ascending.

        """
        # use dictionary to remove dups
        d = {cls.get_mirror_time(): None}
        if not rp or not rp.index:
            rp = cls._data_dir.append(b"increments")
        for inc in rp.get_incfiles_list():
            d[inc.getinctime()] = None
        mirror_meta_rp = cls._data_dir.append(b"mirror_metadata")
        for inc in mirror_meta_rp.get_incfiles_list():
            d[inc.getinctime()] = None
        return_list = list(d.keys())
        return_list.sort()
        return return_list

    @classmethod
    def initialize_rf_cache(cls, mirror_base, inc_base):
        """Set cls.rf_cache to _CachedRF object"""
        inc_list = inc_base.get_incfiles_list()
        rf = RestoreFile(mirror_base, inc_base, inc_list)
        cls.mirror_base, cls.inc_base = mirror_base, inc_base
        cls.root_rf = rf
        cls.rf_cache = _CachedRF(rf)

    @classmethod
    def close_rf_cache(cls):
        """Run anything remaining on _CachedRF object"""
        cls.rf_cache.close()

    @classmethod
    def get_mirror_rorp_iter(cls, rest_time=None, require_metadata=None):
        """Return iter of mirror rps at given restore time

        Usually we can use the metadata file, but if this is
        unavailable, we may have to build it from scratch.

        If the cls._select object is set, use it to filter out the
        unwanted files from the metadata_iter.

        """
        if rest_time is None:
            rest_time = cls._restore_time

        metadata.SetManager()
        rorp_iter = metadata.ManagerObj.GetAtTime(rest_time,
                                                  cls.mirror_base.index)
        if not rorp_iter:
            if require_metadata:
                log.Log.FatalError("Mirror metadata not found")
            log.Log("Mirror metadata not found, reading from directory",
                    log.WARNING)
            rorp_iter = cls._get_rorp_iter_from_rf(cls.root_rf)

        if cls._select:
            rorp_iter = selection.FilterIter(cls._select, rorp_iter)
        return rorp_iter

    @classmethod
    def set_select(cls, target_rp, select_opts, *filelists):
        """Initialize the mirror selection object"""
        if not select_opts:
            return  # nothing to do...
        cls._select = selection.Select(target_rp)
        cls._select.parse_selection_args(select_opts, filelists)

    @classmethod
    def subtract_indices(cls, index, rorp_iter):
        """Subtract index from index of each rorp in rorp_iter

        subtract_indices is necessary because we
        may not be restoring from the root index.

        """
        if index == ():
            return rorp_iter

        def get_iter():
            for rorp in rorp_iter:
                assert rorp.index[:len(index)] == index, (
                    "Path '{ridx}' must be a sub-path of '{idx}'.".format(
                        ridx=rorp.index, idx=index))
                rorp.index = rorp.index[len(index):]
                yield rorp

        return get_iter()

    @classmethod
    def get_diffs(cls, target_iter):
        """Given rorp iter of target files, return diffs

        Here the target_iter doesn't contain any actual data, just
        attribute listings.  Thus any diffs we generate will be
        snapshots.

        """
        mir_iter = cls.subtract_indices(cls.mirror_base.index,
                                        cls.get_mirror_rorp_iter())
        collated = rorpiter.Collate2Iters(mir_iter, target_iter)
        return cls._get_diffs_from_collated(collated)

    @classmethod
    def _get_rest_time(cls, restore_to_time):
        """Return older time, if restore_to_time is in between two inc times

        There is a slightly tricky reason for doing this: The rest of the
        code just ignores increments that are older than restore_to_time.
        But sometimes we want to consider the very next increment older
        than rest time, because rest_time will be between two increments,
        and what was actually on the mirror side will correspond to the
        older one.

        So if restore_to_time is inbetween two increments, return the
        older one.

        """
        inctimes = cls.get_increment_times()
        older_times = [time for time in inctimes if time <= restore_to_time]
        if older_times:
            return max(older_times)
        else:  # restore time older than oldest increment, just return that
            return min(inctimes)

    @classmethod
    def _get_rorp_iter_from_rf(cls, rf):
        """Recursively yield mirror rorps from rf"""
        rorp = rf.get_attribs()
        yield rorp
        if rorp.isdir():
            for sub_rf in rf.yield_sub_rfs():
                for attribs in cls._get_rorp_iter_from_rf(sub_rf):
                    yield attribs

    @classmethod
    def _get_diffs_from_collated(cls, collated):
        """Get diff iterator from collated"""
        for mir_rorp, target_rorp in collated:
            if Globals.preserve_hardlinks and mir_rorp:
                Hardlink.add_rorp(mir_rorp, target_rorp)
            if (not target_rorp or not mir_rorp or not mir_rorp == target_rorp
                    or (Globals.preserve_hardlinks
                        and not Hardlink.rorp_eq(mir_rorp, target_rorp))):
                diff = cls._get_diff(mir_rorp, target_rorp)
            else:
                diff = None
            if Globals.preserve_hardlinks and mir_rorp:
                Hardlink.del_rorp(mir_rorp)
            if diff:
                yield diff

    @classmethod
    def _get_diff(cls, mir_rorp, target_rorp):
        """Get a diff for mir_rorp at time"""
        if not mir_rorp:
            mir_rorp = rpath.RORPath(target_rorp.index)
        elif Globals.preserve_hardlinks and Hardlink.is_linked(mir_rorp):
            mir_rorp.flaglinked(Hardlink.get_link_index(mir_rorp))
        elif mir_rorp.isreg():
            expanded_index = cls.mirror_base.index + mir_rorp.index
            file_fp = cls.rf_cache.get_fp(expanded_index, mir_rorp)
            mir_rorp.setfile(hash.FileWrapper(file_fp))
        mir_rorp.set_attached_filetype('snapshot')
        return mir_rorp


class _CacheCollatedPostProcess:
    """

    Cache a collated iter of (source_rorp, dest_rorp) pairs

    This is necessary for three reasons:

    1.  The patch function may need the original source_rorp or
        dest_rp information, which is not present in the diff it
        receives.

    2.  The metadata must match what is stored in the destination
        directory.  If there is an error, either we do not update the
        dest directory for that file and the old metadata is used, or
        the file is deleted on the other end..  Thus we cannot write
        any metadata until we know the file has been processed
        correctly.

    3.  We may lack permissions on certain destination directories.
        The permissions of these directories need to be relaxed before
        we enter them to computer signatures, and then reset after we
        are done patching everything inside them.

    4.  We need some place to put hashes (like SHA1) after computing
        them and before writing them to the metadata.

    The class caches older source_rorps and dest_rps so the patch
    function can retrieve them if necessary.  The patch function can
    also update the processed correctly flag.  When an item falls out
    of the cache, we assume it has been processed, and write the
    metadata for it.

    """

    def __init__(self, collated_iter, cache_size, dest_root_rp):
        """Initialize new CCWP."""
        self.iter = collated_iter  # generates (source_rorp, dest_rorp) pairs
        self.cache_size = cache_size
        self.dest_root_rp = dest_root_rp

        self.statfileobj = statistics.init_statfileobj()
        if Globals.file_statistics:
            statistics.FileStats.init()
        self.metawriter = metadata.ManagerObj.GetWriter()

        # the following should map indices to lists
        # [source_rorp, dest_rorp, changed_flag, success_flag, increment]

        # changed_flag should be true if the rorps are different, and

        # success_flag should be 1 if dest_rorp has been successfully
        # updated to source_rorp, and 2 if the destination file is
        # deleted entirely.  They both default to false (0).

        # increment holds the RPath of the increment file if one
        # exists.  It is used to record file statistics.

        self.cache_dict = {}
        self.cache_indices = []

        # Contains a list of pairs (destination_rps, permissions) to
        # be used to reset the permissions of certain directories
        # after we're finished with them
        self.dir_perms_list = []

        # Contains list of (index, (source_rorp, diff_rorp)) pairs for
        # the parent directories of the last item in the cache.
        self.parent_list = []

    def __iter__(self):
        return self

    def __next__(self):
        """Return next (source_rorp, dest_rorp) pair.  StopIteration passed"""
        source_rorp, dest_rorp = next(self.iter)
        self._pre_process(source_rorp, dest_rorp)
        index = source_rorp and source_rorp.index or dest_rorp.index
        self.cache_dict[index] = [source_rorp, dest_rorp, 0, 0, None]
        self.cache_indices.append(index)

        if len(self.cache_indices) > self.cache_size:
            self._shorten_cache()
        return source_rorp, dest_rorp

    def in_cache(self, index):
        """Return true if given index is cached"""
        return index in self.cache_dict

    def flag_success(self, index):
        """Signal that the file with given index was updated successfully"""
        self.cache_dict[index][3] = 1

    def flag_deleted(self, index):
        """Signal that the destination file was deleted"""
        self.cache_dict[index][3] = 2

    def flag_changed(self, index):
        """Signal that the file with given index has changed"""
        self.cache_dict[index][2] = 1

    def set_inc(self, index, inc):
        """Set the increment of the current file"""
        self.cache_dict[index][4] = inc

    def get_rorps(self, index):
        """Retrieve (source_rorp, dest_rorp) from cache"""
        try:
            return self.cache_dict[index][:2]
        except KeyError:
            return self._get_parent_rorps(index)

    def get_source_rorp(self, index):
        """Retrieve source_rorp with given index from cache"""
        assert index >= self.cache_indices[0], (
            "CCPP index out of order: {idx!r} shouldn't be less than "
            "{cached!r}.".format(idx=index, cached=self.cache_indices[0]))
        try:
            return self.cache_dict[index][0]
        except KeyError:
            return self._get_parent_rorps(index)[0]

    def get_mirror_rorp(self, index):
        """Retrieve mirror_rorp with given index from cache"""
        try:
            return self.cache_dict[index][1]
        except KeyError:
            return self._get_parent_rorps(index)[1]

    def update_hash(self, index, sha1sum):
        """Update the source rorp's SHA1 hash"""
        self.get_source_rorp(index).set_sha1(sha1sum)

    def update_hardlink_hash(self, diff_rorp):
        """Tag associated source_rorp with same hash diff_rorp points to"""
        sha1sum = Hardlink.get_sha1(diff_rorp)
        if not sha1sum:
            return
        source_rorp = self.get_source_rorp(diff_rorp.index)
        source_rorp.set_sha1(sha1sum)

    def close(self):
        """Process the remaining elements in the cache"""
        while self.cache_indices:
            self._shorten_cache()
        while self.dir_perms_list:
            dir_rp, perms = self.dir_perms_list.pop()
            dir_rp.chmod(perms)
        self.metawriter.close()
        metadata.ManagerObj.ConvertMetaToDiff()

    def _pre_process(self, source_rorp, dest_rorp):
        """Do initial processing on source_rorp and dest_rorp

        It will not be clear whether source_rorp and dest_rorp have
        errors at this point, so don't do anything which assumes they
        will be backed up correctly.

        """
        if Globals.preserve_hardlinks and source_rorp:
            Hardlink.add_rorp(source_rorp, dest_rorp)
        if (dest_rorp and dest_rorp.isdir() and Globals.process_uid != 0
                and dest_rorp.getperms() % 0o1000 < 0o700):
            self._unreadable_dir_init(source_rorp, dest_rorp)

    def _unreadable_dir_init(self, source_rorp, dest_rorp):
        """Initialize an unreadable dir.

        Make it readable, and if necessary, store the old permissions
        in self.dir_perms_list so the old perms can be restored.

        """
        dest_rp = self.dest_root_rp.new_index(dest_rorp.index)
        dest_rp.chmod(0o700 | dest_rorp.getperms())
        if source_rorp and source_rorp.isdir():
            self.dir_perms_list.append((dest_rp, source_rorp.getperms()))

    def _shorten_cache(self):
        """Remove one element from cache, possibly adding it to metadata"""
        first_index = self.cache_indices[0]
        del self.cache_indices[0]
        try:
            (old_source_rorp, old_dest_rorp, changed_flag, success_flag,
             inc) = self.cache_dict[first_index]
        except KeyError:  # probably caused by error in file system (dup)
            log.Log("Index {ix} missing from CCPP cache".format(
                ix=first_index), log.WARNING)
            return
        del self.cache_dict[first_index]
        self._post_process(old_source_rorp, old_dest_rorp, changed_flag,
                           success_flag, inc)
        if self.dir_perms_list:
            self._reset_dir_perms(first_index)
        self._update_parent_list(first_index, old_source_rorp, old_dest_rorp)

    def _update_parent_list(self, index, src_rorp, dest_rorp):
        """Update the parent cache with the recently expired main cache entry

        This method keeps parent directories in the secondary parent
        cache until all their children have expired from the main
        cache.  This is necessary because we may realize we need a
        parent directory's information after we have processed many
        subfiles.

        """
        if not (src_rorp and src_rorp.isdir()
                or dest_rorp and dest_rorp.isdir()):
            return  # neither is directory
        assert self.parent_list or index == (), (
            "Index '{idx}' must be empty if no parent in list".format(
                idx=index))
        if self.parent_list:
            last_parent_index = self.parent_list[-1][0]
            lp_index, li = len(last_parent_index), len(index)
            assert li <= lp_index + 1, (
                "The length of the current index '{idx}' can't be more than "
                "one greater than the last parent's '{pidx}'.".format(
                    idx=index, pidx=last_parent_index))
            # li == lp_index + 1, means we've descended into previous parent
            # if li <= lp_index, we're in a new directory but it must have
            # a common path up to (li - 1) with the last parent
            if li <= lp_index:
                assert last_parent_index[:li - 1] == index[:-1], (
                    "Current index '{idx}' and last parent index '{pidx}' "
                    "must have a common path up to {lvl} levels.".format(
                        idx=index, pidx=last_parent_index, lvl=(li - 1)))
                self.parent_list = self.parent_list[:li]
        self.parent_list.append((index, (src_rorp, dest_rorp)))

    def _post_process(self, source_rorp, dest_rorp, changed, success, inc):
        """Post process source_rorp and dest_rorp.

        The point of this is to write statistics and metadata.

        changed will be true if the files have changed.  success will
        be true if the files have been successfully updated (this is
        always false for un-changed files).

        """
        if Globals.preserve_hardlinks and source_rorp:
            Hardlink.del_rorp(source_rorp)

        if not changed or success:
            if source_rorp:
                self.statfileobj.add_source_file(source_rorp)
            if dest_rorp:
                self.statfileobj.add_dest_file(dest_rorp)
        if success == 0:
            metadata_rorp = dest_rorp
        elif success == 1:
            metadata_rorp = source_rorp
        else:
            metadata_rorp = None  # in case deleted because of ListError
        if success == 1 or success == 2:
            self.statfileobj.add_changed(source_rorp, dest_rorp)

        if metadata_rorp and metadata_rorp.lstat():
            self.metawriter.write_object(metadata_rorp)
        if Globals.file_statistics:
            statistics.FileStats.update(source_rorp, dest_rorp, changed, inc)

    def _reset_dir_perms(self, current_index):
        """Reset the permissions of directories when we have left them"""
        dir_rp, perms = self.dir_perms_list[-1]
        dir_index = dir_rp.index
        if (current_index > dir_index
                and current_index[:len(dir_index)] != dir_index):
            dir_rp.chmod(perms)  # out of directory, reset perms now

    def _get_parent_rorps(self, index):
        """Retrieve (src_rorp, dest_rorp) pair from parent cache"""
        for parent_index, pair in self.parent_list:
            if parent_index == index:
                return pair
        raise KeyError(index)


class _RepoPatchITRB(rorpiter.ITRBranch):
    """Patch an rpath with the given diff iters (use with IterTreeReducer)

    The main complication here involves directories.  We have to
    finish processing the directory after what's in the directory, as
    the directory may have inappropriate permissions to alter the
    contents or the dir's mtime could change as we change the
    contents.

    """

    def __init__(self, basis_root_rp, CCPP):
        """Set basis_root_rp, the base of the tree to be incremented"""
        self.basis_root_rp = basis_root_rp
        assert basis_root_rp.conn is Globals.local_connection, (
            "Basis root path connection {conn} isn't "
            "local connection {lconn}.".format(
                conn=basis_root_rp.conn, lconn=Globals.local_connection))
        self.statfileobj = (statistics.get_active_statfileobj()
                            or statistics.StatFileObj())
        self.dir_replacement, self.dir_update = None, None
        self.CCPP = CCPP
        self.error_handler = robust.get_error_handler("UpdateError")

    def can_fast_process(self, index, diff_rorp):
        """True if diff_rorp and mirror are not directories"""
        mirror_rorp = self.CCPP.get_mirror_rorp(index)
        return not (diff_rorp.isdir() or (mirror_rorp and mirror_rorp.isdir()))

    def fast_process_file(self, index, diff_rorp):
        """Patch base_rp with diff_rorp (case where neither is directory)"""
        mirror_rp, discard = longname.get_mirror_inc_rps(
            self.CCPP.get_rorps(index), self.basis_root_rp)
        assert not mirror_rp.isdir(), (
            "Mirror path '{rp}' points to a directory.".format(rp=mirror_rp))
        tf = mirror_rp.get_temp_rpath(sibling=True)
        if self._patch_to_temp(mirror_rp, diff_rorp, tf):
            if tf.lstat():
                if robust.check_common_error(self.error_handler, rpath.rename,
                                             (tf, mirror_rp)) is None:
                    self.CCPP.flag_success(index)
                else:
                    tf.delete()
            elif mirror_rp and mirror_rp.lstat():
                mirror_rp.delete()
                self.CCPP.flag_deleted(index)
        else:
            tf.setdata()
            if tf.lstat():
                tf.delete()

    def start_process_directory(self, index, diff_rorp):
        """Start processing directory - record information for later"""
        self.base_rp, discard = longname.get_mirror_inc_rps(
            self.CCPP.get_rorps(index), self.basis_root_rp)
        if diff_rorp.isdir():
            self._prepare_dir(diff_rorp, self.base_rp)
        elif self._set_dir_replacement(diff_rorp, self.base_rp):
            if diff_rorp.lstat():
                self.CCPP.flag_success(index)
            else:
                self.CCPP.flag_deleted(index)

    def end_process_directory(self):
        """Finish processing directory"""
        if self.dir_update:
            assert self.base_rp.isdir(), (
                "Base directory '{rp}' isn't a directory.".format(
                    rp=self.base_rp))
            rpath.copy_attribs(self.dir_update, self.base_rp)

            if (Globals.process_uid != 0
                    and self.dir_update.getperms() % 0o1000 < 0o700):
                # Directory was unreadable at start -- keep it readable
                # until the end of the backup process.
                self.base_rp.chmod(0o700 | self.dir_update.getperms())
        elif self.dir_replacement:
            self.base_rp.rmdir()
            if self.dir_replacement.lstat():
                rpath.rename(self.dir_replacement, self.base_rp)

    def _patch_to_temp(self, basis_rp, diff_rorp, new):
        """Patch basis_rp, writing output in new, which doesn't exist yet

        Returns true if able to write new as desired, false if
        UpdateError or similar gets in the way.

        """
        if diff_rorp.isflaglinked():
            self._patch_hardlink_to_temp(diff_rorp, new)
        elif diff_rorp.get_attached_filetype() == 'snapshot':
            result = self._patch_snapshot_to_temp(diff_rorp, new)
            if not result:
                return 0
            elif result == 2:
                return 1  # SpecialFile
        elif not self._patch_diff_to_temp(basis_rp, diff_rorp, new):
            return 0
        if new.lstat():
            if diff_rorp.isflaglinked():
                if Globals.eas_write:
                    """ `isflaglinked() == True` implies that we are processing
                    the 2nd (or later) file in a group of files linked to an
                    inode.  As such, we don't need to perform the usual
                    `copy_attribs(diff_rorp, new)` for the inode because that
                    was already done when the 1st file in the group was
                    processed.  Nonetheless, we still must perform the following
                    task (which would have normally been performed by
                    `copy_attribs()`).  Otherwise, the subsequent call to
                    `_matches_cached_rorp(diff_rorp, new)` will fail because the
                    new rorp's metadata would be missing the extended attribute
                    data.
                    """
                    new.data['ea'] = diff_rorp.get_ea()
            else:
                rpath.copy_attribs(diff_rorp, new)
        return self._matches_cached_rorp(diff_rorp, new)

    def _patch_hardlink_to_temp(self, diff_rorp, new):
        """Hardlink diff_rorp to temp, update hash if necessary"""
        Hardlink.link_rp(diff_rorp, new, self.basis_root_rp)
        self.CCPP.update_hardlink_hash(diff_rorp)

    def _patch_snapshot_to_temp(self, diff_rorp, new):
        """Write diff_rorp to new, return true if successful

        Returns 1 if normal success, 2 if special file is written,
        whether or not it is successful.  This is because special
        files either fail with a SpecialFileError, or don't need to be
        compared.

        """
        if diff_rorp.isspecial():
            self._write_special(diff_rorp, new)
            rpath.copy_attribs(diff_rorp, new)
            return 2

        report = robust.check_common_error(self.error_handler, rpath.copy,
                                           (diff_rorp, new))
        if isinstance(report, hash.Report):
            self.CCPP.update_hash(diff_rorp.index, report.sha1_digest)
            return 1
        return report != 0  # if == 0, error_handler caught something

    def _patch_diff_to_temp(self, basis_rp, diff_rorp, new):
        """Apply diff_rorp to basis_rp, write output in new"""
        assert diff_rorp.get_attached_filetype() == 'diff', (
            "Type attached to '{rp}' isn't '{exp}' but '{att}'.".format(
                rp=diff_rorp, exp="diff",
                att=diff_rorp.get_attached_filetype()))
        report = robust.check_common_error(
            self.error_handler, Rdiff.patch_local, (basis_rp, diff_rorp, new))
        if isinstance(report, hash.Report):
            self.CCPP.update_hash(diff_rorp.index, report.sha1_digest)
            return 1
        return report != 0  # if report == 0, error

    def _matches_cached_rorp(self, diff_rorp, new_rp):
        """Return true if new_rp matches cached src rorp

        This is a final check to make sure the temp file just written
        matches the stats which we got earlier.  If it doesn't it
        could confuse the regress operation.  This is only necessary
        for regular files.

        """
        if not new_rp.isreg():
            return 1
        cached_rorp = self.CCPP.get_source_rorp(diff_rorp.index)
        if cached_rorp and cached_rorp.equal_loose(new_rp):
            return 1
        log.ErrorLog.write_if_open(
            "UpdateError", diff_rorp, "Updated mirror "
            "temp file '{tf}' does not match source".format(tf=new_rp))
        return 0

    def _write_special(self, diff_rorp, new):
        """Write diff_rorp (which holds special file) to new"""
        eh = robust.get_error_handler("SpecialFileError")
        if robust.check_common_error(eh, rpath.copy, (diff_rorp, new)) == 0:
            new.setdata()
            if new.lstat():
                new.delete()
            new.touch()

    def _set_dir_replacement(self, diff_rorp, base_rp):
        """Set self.dir_replacement, which holds data until done with dir

        This is used when base_rp is a dir, and diff_rorp is not.
        Returns 1 for success or 0 for failure

        """
        assert diff_rorp.get_attached_filetype() == 'snapshot', (
            "Type attached to '{rp}' isn't '{exp}' but '{att}'.".format(
                rp=diff_rorp, exp="snapshot",
                att=diff_rorp.get_attached_filetype()))
        self.dir_replacement = base_rp.get_temp_rpath(sibling=True)
        if not self._patch_to_temp(None, diff_rorp, self.dir_replacement):
            if self.dir_replacement.lstat():
                self.dir_replacement.delete()
            # Was an error, so now restore original directory
            rpath.copy_with_attribs(
                self.CCPP.get_mirror_rorp(diff_rorp.index),
                self.dir_replacement)
            return 0
        else:
            return 1

    def _prepare_dir(self, diff_rorp, base_rp):
        """Prepare base_rp to be a directory"""
        self.dir_update = diff_rorp.getRORPath()  # make copy in case changes
        if not base_rp.isdir():
            if base_rp.lstat():
                self.base_rp.delete()
            base_rp.setdata()
            base_rp.mkdir()
            self.CCPP.flag_success(diff_rorp.index)
        else:  # maybe no change, so query CCPP before tagging success
            if self.CCPP.in_cache(diff_rorp.index):
                self.CCPP.flag_success(diff_rorp.index)


class _RepoIncrementITRB(_RepoPatchITRB):
    """
    Patch an rpath with the given diff iters and write increments

    Like _RepoPatchITRB, but this time also write increments.
    """

    def __init__(self, basis_root_rp, inc_root_rp, rorp_cache):
        self.inc_root_rp = inc_root_rp
        _RepoPatchITRB.__init__(self, basis_root_rp, rorp_cache)

    def fast_process_file(self, index, diff_rorp):
        """Patch base_rp with diff_rorp and write increment (neither is dir)"""
        mirror_rp, inc_prefix = longname.get_mirror_inc_rps(
            self.CCPP.get_rorps(index), self.basis_root_rp, self.inc_root_rp)
        tf = mirror_rp.get_temp_rpath(sibling=True)
        if self._patch_to_temp(mirror_rp, diff_rorp, tf):
            inc = robust.check_common_error(self.error_handler,
                                            increment.Increment,
                                            (tf, mirror_rp, inc_prefix))
            if inc is not None and not isinstance(inc, int):
                self.CCPP.set_inc(index, inc)
                if inc.isreg():
                    inc.fsync_with_dir()  # Write inc before rp changed
                if tf.lstat():
                    if robust.check_common_error(self.error_handler,
                                                 rpath.rename,
                                                 (tf, mirror_rp)) is None:
                        self.CCPP.flag_success(index)
                    else:
                        tf.delete()
                elif mirror_rp.lstat():
                    mirror_rp.delete()
                    self.CCPP.flag_deleted(index)
                return  # normal return, otherwise error occurred
        tf.setdata()
        if tf.lstat():
            tf.delete()

    def start_process_directory(self, index, diff_rorp):
        """Start processing directory"""
        self.base_rp, inc_prefix = longname.get_mirror_inc_rps(
            self.CCPP.get_rorps(index), self.basis_root_rp, self.inc_root_rp)
        self.base_rp.setdata()
        assert diff_rorp.isdir() or self.base_rp.isdir(), (
            "Either diff '{ipath!r}' or base '{bpath!r}' "
            "must be a directory".format(ipath=diff_rorp, bpath=self.base_rp))
        if diff_rorp.isdir():
            inc = increment.Increment(diff_rorp, self.base_rp, inc_prefix)
            if inc and inc.isreg():
                inc.fsync_with_dir()  # must write inc before rp changed
            self.base_rp.setdata()  # in case written by increment above
            self._prepare_dir(diff_rorp, self.base_rp)
        elif self._set_dir_replacement(diff_rorp, self.base_rp):
            inc = increment.Increment(self.dir_replacement, self.base_rp,
                                      inc_prefix)
            if inc:
                self.CCPP.set_inc(index, inc)
                self.CCPP.flag_success(index)


class _CachedRF:
    """Store RestoreFile objects until they are needed

    The code above would like to pretend it has random access to RFs,
    making one for a particular index at will.  However, in general
    this involves listing and filtering a directory, which can get
    expensive.

    Thus, when a _CachedRF retrieves an RestoreFile, it creates all the
    RFs of that directory at the same time, and doesn't have to
    recalculate.  It assumes the indices will be in order, so the
    cache is deleted if a later index is requested.

    """

    def __init__(self, root_rf):
        """Initialize _CachedRF, self.rf_list variable"""
        self.root_rf = root_rf
        self.rf_list = []  # list should filled in index order
        if Globals.process_uid != 0:
            self.perm_changer = _PermissionChanger(root_rf.mirror_rp)

    def get_fp(self, index, mir_rorp):
        """Return the file object (for reading) of given index"""
        rf = longname.update_rf(self._get_rf(index, mir_rorp), mir_rorp,
                                self.root_rf.mirror_rp, RestoreFile)
        if not rf:
            log.Log(
                "Unable to retrieve data for file {fi}! The cause is "
                "probably data loss from the backup repository".format(
                    fi=(index and "/".join(index) or '.')), log.WARNING)
            return io.BytesIO()
        return rf.get_restore_fp()

    def close(self):
        """Finish remaining rps in _PermissionChanger"""
        if Globals.process_uid != 0:
            self.perm_changer.finish()

    def _get_rf(self, index, mir_rorp=None):
        """Get a RestoreFile for given index, or None"""
        while 1:
            if not self.rf_list:
                if not self._add_rfs(index, mir_rorp):
                    return None
            rf = self.rf_list[0]
            if rf.index == index:
                if Globals.process_uid != 0:
                    self.perm_changer(index, mir_rorp)
                return rf
            elif rf.index > index:
                # Try to add earlier indices.  But if first is
                # already from same directory, or we can't find any
                # from that directory, then we know it can't be added.
                if (index[:-1] == rf.index[:-1]
                        or not self._add_rfs(index, mir_rorp)):
                    return None
            else:
                del self.rf_list[0]

    def _add_rfs(self, index, mir_rorp=None):
        """Given index, add the rfs in that same directory

        Returns false if no rfs are available, which usually indicates
        an error.

        """
        if not index:
            return self.root_rf
        if mir_rorp.has_alt_mirror_name():
            return  # longname alias separate
        parent_index = index[:-1]
        if Globals.process_uid != 0:
            self.perm_changer(parent_index)
        temp_rf = RestoreFile(
            self.root_rf.mirror_rp.new_index(parent_index),
            self.root_rf.inc_rp.new_index(parent_index), [])
        new_rfs = list(temp_rf.yield_sub_rfs())
        if not new_rfs:
            return 0
        self.rf_list[0:0] = new_rfs
        return 1

    def _debug_list_rfs_in_cache(self, index):
        """Used for debugging, return indices of cache rfs for printing"""
        s1 = "-------- Cached RF for %s -------" % (index, )
        s2 = " ".join([str(rf.index) for rf in self.rf_list])
        s3 = "--------------------------"
        return "\n".join((s1, s2, s3))


class RestoreFile:
    """
    Hold data about a single mirror file and its related increments

    self.relevant_incs will be set to a list of increments that matter
    for restoring a regular file.  If the patches are to mirror_rp, it
    will be the first element in self.relevant.incs
    """

    def __init__(self, mirror_rp, inc_rp, inc_list):
        self.index = mirror_rp.index
        self.mirror_rp = mirror_rp
        self.inc_rp, self.inc_list = inc_rp, inc_list
        self.set_relevant_incs()

    def __str__(self):
        return "Index: %s, Mirror: %s, Increment: %s\nIncList: %s\nIncRel: %s" % (
            self.index, self.mirror_rp, self.inc_rp,
            list(map(str, self.inc_list)), list(map(str, self.relevant_incs)))

    @classmethod
    def initialize(cls, restore_time, mirror_time):
        """
        Initialize the RestoreFile class with restore and mirror time
        """
        cls._restore_time = restore_time
        cls._mirror_time = mirror_time

    def set_relevant_incs(self):
        """
        Set self.relevant_incs to increments that matter for restoring

        relevant_incs is sorted newest first.  If mirror_rp matters,
        it will be (first) in relevant_incs.
        """
        self.mirror_rp.inc_type = b'snapshot'
        self.mirror_rp.inc_compressed = 0
        if (not self.inc_list or self._restore_time >= self._mirror_time):
            self.relevant_incs = [self.mirror_rp]
            return

        newer_incs = self.get_newer_incs()
        i = 0
        while (i < len(newer_incs)):
            # Only diff type increments require later versions
            if newer_incs[i].getinctype() != b"diff":
                break
            i = i + 1
        self.relevant_incs = newer_incs[:i + 1]
        if (not self.relevant_incs
                or self.relevant_incs[-1].getinctype() == b"diff"):
            self.relevant_incs.append(self.mirror_rp)
        self.relevant_incs.reverse()  # return in reversed order

    def get_newer_incs(self):
        """
        Return list of newer incs sorted by time (increasing)

        Also discard increments older than rest_time (rest_time we are
        assuming is the exact time rdiff-backup was run, so no need to
        consider the next oldest increment or any of that)
        """
        incpairs = []
        for inc in self.inc_list:
            time = inc.getinctime()
            if time >= self._restore_time:
                incpairs.append((time, inc))
        incpairs.sort()
        return [pair[1] for pair in incpairs]

    def get_attribs(self):
        """Return RORP with restored attributes, but no data

        This should only be necessary if the metadata file is lost for
        some reason.  Otherwise the file provides all data.  The size
        will be wrong here, because the attribs may be taken from
        diff.

        """
        last_inc = self.relevant_incs[-1]
        if last_inc.getinctype() == b'missing':
            return rpath.RORPath(self.index)

        rorp = last_inc.getRORPath()
        rorp.index = self.index
        if last_inc.getinctype() == b'dir':
            rorp.data['type'] = 'dir'
        return rorp

    def get_restore_fp(self):
        """Return file object of restored data"""

        def get_fp():
            current_fp = self._get_first_fp()
            for inc_diff in self.relevant_incs[1:]:
                log.Log("Applying patch file {pf}".format(pf=inc_diff),
                        log.DEBUG)
                assert inc_diff.getinctype() == b'diff', (
                    "Path '{irp!r}' must be of type 'diff'.".format(
                        irp=inc_diff))
                delta_fp = inc_diff.open("rb", inc_diff.isinccompressed())
                new_fp = tempfile.TemporaryFile()
                Rdiff.write_patched_fp(current_fp, delta_fp, new_fp)
                new_fp.seek(0)
                current_fp = new_fp
            return current_fp

        def error_handler(exc):
            log.Log("Failed reading file {fi}, substituting empty file.".format(
                fi=self.mirror_rp), log.WARNING)
            return io.BytesIO(b'')

        if not self.relevant_incs[-1].isreg():
            log.Log("""Could not restore file {rf}!

A regular file was indicated by the metadata, but could not be
constructed from existing increments because last increment had type {it}.
Instead of the actual file's data, an empty length file will be created.
This error is probably caused by data loss in the
rdiff-backup destination directory, or a bug in rdiff-backup""".format(
                rf=self.mirror_rp,
                it=self.relevant_incs[-1].lstat()), log.WARNING)
            return io.BytesIO()
        return robust.check_common_error(error_handler, get_fp)

    def yield_sub_rfs(self):
        """Return RestoreFiles under current RestoreFile (which is dir)"""
        if not self.mirror_rp.isdir() and not self.inc_rp.isdir():
            return
        if self.mirror_rp.isdir():
            mirror_iter = self._yield_mirrorrps(self.mirror_rp)
        else:
            mirror_iter = iter([])
        if self.inc_rp.isdir():
            inc_pair_iter = self.yield_inc_complexes(self.inc_rp)
        else:
            inc_pair_iter = iter([])
        collated = rorpiter.Collate2Iters(mirror_iter, inc_pair_iter)

        for mirror_rp, inc_pair in collated:
            if not inc_pair:
                inc_rp = self.inc_rp.new_index(mirror_rp.index)
                inc_list = []
            else:
                inc_rp, inc_list = inc_pair
            if not mirror_rp:
                mirror_rp = self.mirror_rp.new_index_empty(inc_rp.index)
            yield self.__class__(mirror_rp, inc_rp, inc_list)

    def yield_inc_complexes(self, inc_rpath):
        """Yield (sub_inc_rpath, inc_list) IndexedTuples from given inc_rpath

        Finds pairs under directory inc_rpath.  sub_inc_rpath will just be
        the prefix rp, while the rps in inc_list should actually exist.

        """
        if not inc_rpath.isdir():
            return

        def get_inc_pairs():
            """Return unsorted list of (basename, inc_filenames) pairs"""
            inc_dict = {}  # dictionary of basenames:inc_filenames
            dirlist = robust.listrp(inc_rpath)

            def add_to_dict(filename):
                """Add filename to the inc tuple dictionary"""
                rp = inc_rpath.append(filename)
                if rp.isincfile() and rp.getinctype() != b'data':
                    basename = rp.getincbase_bname()
                    inc_filename_list = inc_dict.setdefault(basename, [])
                    inc_filename_list.append(filename)
                elif rp.isdir():
                    inc_dict.setdefault(filename, [])

            for filename in dirlist:
                add_to_dict(filename)
            return list(inc_dict.items())

        def inc_filenames2incrps(filenames):
            """Map list of filenames into increment rps"""
            inc_list = []
            for filename in filenames:
                rp = inc_rpath.append(filename)
                assert rp.isincfile(), (
                    "Path '{mrp}' must be an increment file.".format(mrp=rp))
                inc_list.append(rp)
            return inc_list

        items = get_inc_pairs()
        items.sort()  # Sorting on basis of basename now
        for (basename, inc_filenames) in items:
            sub_inc_rpath = inc_rpath.append(basename)
            yield rorpiter.IndexedTuple(
                sub_inc_rpath.index,
                (sub_inc_rpath, inc_filenames2incrps(inc_filenames)))

    def _get_first_fp(self):
        """Return first file object from relevant inc list"""
        first_inc = self.relevant_incs[0]
        assert first_inc.getinctype() == b'snapshot', (
            "Path '{srp}' must be of type 'snapshot'.".format(
                srp=first_inc))
        if not first_inc.isinccompressed():
            return first_inc.open("rb")

        # current_fp must be a real (uncompressed) file
        current_fp = tempfile.TemporaryFile()
        fp = first_inc.open("rb", compress=1)
        rpath.copyfileobj(fp, current_fp)
        fp.close()
        current_fp.seek(0)
        return current_fp

    def _yield_mirrorrps(self, mirrorrp):
        """Yield mirrorrps underneath given mirrorrp"""
        assert mirrorrp.isdir(), (
            "Mirror path '{mrp}' must be a directory.".format(mrp=mirrorrp))
        for filename in robust.listrp(mirrorrp):
            rp = mirrorrp.append(filename)
            if rp.index != (b'rdiff-backup-data', ):
                yield rp

    def _debug_relevant_incs_string(self):
        """Return printable string of relevant incs, used for debugging"""
        inc_header = ["---- Relevant incs for %s" % ("/".join(self.index), )]
        inc_header.extend([
            "{itp} {ils} {irp}".format(
                itp=inc.getinctype(), ils=inc.lstat(), irp=inc)
            for inc in self.relevant_incs
        ])
        inc_header.append("--------------------------------")
        return "\n".join(inc_header)


class _PermissionChanger:
    """Change the permission of mirror files and directories

    The problem is that mirror files and directories may need their
    permissions changed in order to be read and listed, and then
    changed back when we are done.  This class hooks into the _CachedRF
    object to know when an rp is needed.

    """

    def __init__(self, root_rp):
        self.root_rp = root_rp
        self.current_index = ()
        # Below is a list of (index, rp, old_perm) triples in reverse
        # order that need clearing
        self.open_index_list = []

    def __call__(self, index, mir_rorp=None):
        """Given rpath, change permissions up to and including index"""
        if mir_rorp and mir_rorp.has_alt_mirror_name():
            return
        old_index = self.current_index
        self.current_index = index
        if not index or index <= old_index:
            return
        self._restore_old(index)
        self._add_chmod_new(old_index, index)

    def finish(self):
        """Restore any remaining rps"""
        for index, rp, perms in self.open_index_list:
            rp.chmod(perms)

    def _restore_old(self, index):
        """Restore permissions for indices we are done with"""
        while self.open_index_list:
            old_index, old_rp, old_perms = self.open_index_list[0]
            if index[:len(old_index)] > old_index:
                old_rp.chmod(old_perms)
            else:
                break
            del self.open_index_list[0]

    def _add_chmod_new(self, old_index, index):
        """Change permissions of directories between old_index and index"""
        for rp in self._get_new_rp_list(old_index, index):
            if ((rp.isreg() and not rp.readable())
                    or (rp.isdir() and not (rp.executable() and rp.readable()))):
                old_perms = rp.getperms()
                self.open_index_list.insert(0, (rp.index, rp, old_perms))
                if rp.isreg():
                    rp.chmod(0o400 | old_perms)
                else:
                    rp.chmod(0o700 | old_perms)

    def _get_new_rp_list(self, old_index, index):
        """Return list of new rp's between old_index and index

        Do this lazily so that the permissions on the outer
        directories are fixed before we need the inner dirs.

        """
        for i in range(len(index) - 1, -1, -1):
            if old_index[:i] == index[:i]:
                common_prefix_len = i
                break  # latest with i==0 does the break happen

        for total_len in range(common_prefix_len + 1, len(index) + 1):
            yield self.root_rp.new_index(index[:total_len])