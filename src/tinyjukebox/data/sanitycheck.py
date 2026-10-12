"""Consistency checks of tags, targets and activities."""

import logging

from tinyjukebox.core.logging import get_child
from tinyjukebox.data.activities import Activities
from tinyjukebox.data.common import is_unmapped
from tinyjukebox.data.tags import Tags
from tinyjukebox.data.targets import Targets

__all__ = ["SanityCheck"]


class SanityCheck:
    """Checks every collection and the references between them; every problem is logged as warning."""

    def __init__(self, logger: logging.Logger, targets: Targets, tags: Tags, activities: Activities) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self.targets = targets
        self.tags = tags
        self.activities = activities

    def sanity_check(self) -> bool:
        targets_passed, targets_errors = self.targets.sanity_check()
        if not targets_passed:
            self._logger.warning("sanity_check - targets: %s", targets_errors)
        # evaluate all checks (no short circuit) so that every problem gets logged
        results = [
            self.tags.sanity_check(),
            targets_passed,
            self.activities.sanity_check(),
            self._cross_check_tags_and_activity(),
            self._cross_check_tags_and_targets(),
        ]
        passed = all(results)
        if not passed:
            self._logger.warning("sanity_check - sanity check failed")
        return passed

    def _cross_check_tags_and_activity(self) -> bool:
        uids_tags = {tag.tag_uid for tag in self.tags.all()}
        uids_activity = set(self.activities.all())
        only_in_activity = uids_activity - uids_tags
        only_in_tags = uids_tags - uids_activity
        if only_in_activity:
            self._logger.warning(
                "_cross_check_jsons - activity json contains uids that are not in tags json:%s", only_in_activity
            )
        if only_in_tags:
            # activities are created on first playback - missing entries are expected for new or unmapped tags
            self._logger.info(
                "_cross_check_jsons - tags json contains uids that are not in activity json:%s", only_in_tags
            )
        return not only_in_activity

    def _cross_check_tags_and_targets(self) -> bool:
        passed = True
        for tag in self.tags.all():
            if is_unmapped(tag.target_id):
                continue
            target = self.targets.targets.get(str(tag.target_id))
            if target is None or target.target_type != tag.target_type:
                passed = False
                target_type = tag.target_type.name if tag.target_type is not None else None
                self._logger.warning(
                    "_cross_check_jsons - tag '%s' contains not existing %s target_id '%s'",
                    tag.tag_uid,
                    target_type,
                    tag.target_id,
                )
        return passed
