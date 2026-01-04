"""
Tests for the TranscodingJobStateMachine.

Verifies that the state machine correctly determines job states from
field combinations and generates proper SQL conditions.
"""

from datetime import datetime, timedelta, timezone

from api.job_state import JobRow, JobState, TranscodingJobStateMachine, job_state_machine


class TestJobRow:
    """Tests for JobRow dataclass."""

    def test_from_mapping_with_all_fields(self):
        """Test JobRow creation from a complete mapping."""
        now = datetime.now(timezone.utc)
        row = {
            "claimed_at": now,
            "claim_expires_at": now + timedelta(minutes=30),
            "completed_at": None,
            "last_error": None,
            "attempt_number": 1,
            "max_attempts": 3,
        }

        job = JobRow.from_mapping(row)

        assert job.claimed_at == now
        assert job.claim_expires_at == now + timedelta(minutes=30)
        assert job.completed_at is None
        assert job.last_error is None
        assert job.attempt_number == 1
        assert job.max_attempts == 3

    def test_from_mapping_with_defaults(self):
        """Test JobRow creation with missing fields uses defaults."""
        row = {
            "claimed_at": None,
            "claim_expires_at": None,
            "completed_at": None,
            "last_error": None,
        }

        job = JobRow.from_mapping(row)

        assert job.attempt_number == 1  # default
        assert job.max_attempts == 3  # default


class TestStatePredicates:
    """Tests for state predicate methods."""

    def test_is_unclaimed_true(self):
        """Test is_unclaimed returns True for unclaimed jobs."""
        job = JobRow(
            claimed_at=None,
            claim_expires_at=None,
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.is_unclaimed(job) is True

    def test_is_unclaimed_false_when_claimed(self):
        """Test is_unclaimed returns False when job is claimed."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=now,
            claim_expires_at=now + timedelta(minutes=30),
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.is_unclaimed(job) is False

    def test_is_unclaimed_false_when_completed(self):
        """Test is_unclaimed returns False when job is completed."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=None,
            claim_expires_at=None,
            completed_at=now,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.is_unclaimed(job) is False

    def test_is_claimed_true_with_active_claim(self):
        """Test is_claimed returns True for active claims."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=now - timedelta(minutes=5),
            claim_expires_at=now + timedelta(minutes=25),
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.is_claimed(job, now=now) is True

    def test_is_claimed_false_when_expired(self):
        """Test is_claimed returns False when claim expired."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=now - timedelta(minutes=35),
            claim_expires_at=now - timedelta(minutes=5),
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.is_claimed(job, now=now) is False

    def test_is_claimed_false_when_completed(self):
        """Test is_claimed returns False when job is completed."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=now - timedelta(minutes=5),
            claim_expires_at=now + timedelta(minutes=25),
            completed_at=now,  # Completed
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.is_claimed(job, now=now) is False

    def test_is_expired_true(self):
        """Test is_expired returns True for expired claims."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=now - timedelta(minutes=35),
            claim_expires_at=now - timedelta(minutes=5),
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.is_expired(job, now=now) is True

    def test_is_expired_false_when_active(self):
        """Test is_expired returns False for active claims."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=now - timedelta(minutes=5),
            claim_expires_at=now + timedelta(minutes=25),
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.is_expired(job, now=now) is False

    def test_is_expired_false_when_never_claimed(self):
        """Test is_expired returns False when job was never claimed."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=None,
            claim_expires_at=None,
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.is_expired(job, now=now) is False

    def test_is_completed_true(self):
        """Test is_completed returns True when completed."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=now - timedelta(hours=1),
            claim_expires_at=now - timedelta(minutes=30),
            completed_at=now,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.is_completed(job) is True

    def test_is_completed_false(self):
        """Test is_completed returns False when not completed."""
        job = JobRow(
            claimed_at=None,
            claim_expires_at=None,
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.is_completed(job) is False

    def test_is_failed_true_when_max_attempts_reached(self):
        """Test is_failed returns True when max attempts reached."""
        job = JobRow(
            claimed_at=None,
            claim_expires_at=None,
            completed_at=None,
            last_error="Transcoding failed: out of memory",
            attempt_number=3,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.is_failed(job) is True

    def test_is_failed_true_when_over_max_attempts(self):
        """Test is_failed returns True when over max attempts."""
        job = JobRow(
            claimed_at=None,
            claim_expires_at=None,
            completed_at=None,
            last_error="Repeated failure",
            attempt_number=5,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.is_failed(job) is True

    def test_is_failed_false_when_attempts_remaining(self):
        """Test is_failed returns False when attempts remain."""
        job = JobRow(
            claimed_at=None,
            claim_expires_at=None,
            completed_at=None,
            last_error="Temporary failure",
            attempt_number=2,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.is_failed(job) is False

    def test_is_failed_false_when_no_error(self):
        """Test is_failed returns False when no error."""
        job = JobRow(
            claimed_at=None,
            claim_expires_at=None,
            completed_at=None,
            last_error=None,
            attempt_number=3,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.is_failed(job) is False

    def test_is_retrying_true(self):
        """Test is_retrying returns True for retriable failures."""
        job = JobRow(
            claimed_at=None,  # Not claimed anymore
            claim_expires_at=None,
            completed_at=None,
            last_error="Temporary error",
            attempt_number=2,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.is_retrying(job) is True

    def test_is_retrying_false_when_claimed(self):
        """Test is_retrying returns False when still claimed."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=now,  # Still claimed
            claim_expires_at=now + timedelta(minutes=30),
            completed_at=None,
            last_error="Error during processing",
            attempt_number=2,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.is_retrying(job) is False

    def test_is_retrying_false_when_max_attempts_reached(self):
        """Test is_retrying returns False when max attempts reached."""
        job = JobRow(
            claimed_at=None,
            claim_expires_at=None,
            completed_at=None,
            last_error="Final failure",
            attempt_number=3,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.is_retrying(job) is False


class TestGetState:
    """Tests for get_state method."""

    def test_get_state_unclaimed(self):
        """Test get_state returns UNCLAIMED for unclaimed jobs."""
        job = JobRow(
            claimed_at=None,
            claim_expires_at=None,
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.get_state(job) == JobState.UNCLAIMED

    def test_get_state_claimed(self):
        """Test get_state returns CLAIMED for actively claimed jobs."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=now - timedelta(minutes=5),
            claim_expires_at=now + timedelta(minutes=25),
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.get_state(job, now=now) == JobState.CLAIMED

    def test_get_state_expired(self):
        """Test get_state returns EXPIRED for expired claims."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=now - timedelta(minutes=35),
            claim_expires_at=now - timedelta(minutes=5),
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.get_state(job, now=now) == JobState.EXPIRED

    def test_get_state_completed(self):
        """Test get_state returns COMPLETED for completed jobs."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=now - timedelta(hours=1),
            claim_expires_at=now - timedelta(minutes=30),
            completed_at=now,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.get_state(job, now=now) == JobState.COMPLETED

    def test_get_state_failed(self):
        """Test get_state returns FAILED for permanently failed jobs."""
        job = JobRow(
            claimed_at=None,
            claim_expires_at=None,
            completed_at=None,
            last_error="Permanent failure",
            attempt_number=3,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.get_state(job) == JobState.FAILED

    def test_get_state_retrying(self):
        """Test get_state returns RETRYING for retriable jobs."""
        job = JobRow(
            claimed_at=None,
            claim_expires_at=None,
            completed_at=None,
            last_error="Temporary failure",
            attempt_number=2,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.get_state(job) == JobState.RETRYING

    def test_get_state_with_mapping(self):
        """Test get_state works with dict mappings."""
        row = {
            "claimed_at": None,
            "claim_expires_at": None,
            "completed_at": None,
            "last_error": None,
            "attempt_number": 1,
            "max_attempts": 3,
        }
        sm = TranscodingJobStateMachine()

        assert sm.get_state(row) == JobState.UNCLAIMED


class TestSqlConditions:
    """Tests for SQL condition generator methods."""

    def test_sql_unclaimed(self):
        """Test SQL condition for unclaimed jobs."""
        sm = TranscodingJobStateMachine()
        condition = sm.sql_unclaimed()

        assert "claimed_at IS NULL" in condition
        assert "completed_at IS NULL" in condition
        assert "AND" in condition

    def test_sql_unclaimed_with_alias(self):
        """Test SQL condition uses custom table alias."""
        sm = TranscodingJobStateMachine()
        condition = sm.sql_unclaimed(table_alias="jobs")

        assert "jobs.claimed_at IS NULL" in condition
        assert "jobs.completed_at IS NULL" in condition

    def test_sql_claimed(self):
        """Test SQL condition for claimed jobs."""
        sm = TranscodingJobStateMachine()
        condition = sm.sql_claimed()

        assert "claimed_at IS NOT NULL" in condition
        assert "claim_expires_at >" in condition
        assert "completed_at IS NULL" in condition

    def test_sql_claimed_with_custom_param(self):
        """Test SQL condition uses custom timestamp parameter."""
        sm = TranscodingJobStateMachine()
        condition = sm.sql_claimed(now_param=":current_time")

        assert ":current_time" in condition

    def test_sql_expired(self):
        """Test SQL condition for expired claims."""
        sm = TranscodingJobStateMachine()
        condition = sm.sql_expired()

        assert "claimed_at IS NOT NULL" in condition
        assert "claim_expires_at <=" in condition
        assert "completed_at IS NULL" in condition

    def test_sql_completed(self):
        """Test SQL condition for completed jobs."""
        sm = TranscodingJobStateMachine()
        condition = sm.sql_completed()

        assert "completed_at IS NOT NULL" in condition

    def test_sql_failed(self):
        """Test SQL condition for failed jobs."""
        sm = TranscodingJobStateMachine()
        condition = sm.sql_failed()

        assert "last_error IS NOT NULL" in condition
        assert "attempt_number >= " in condition
        assert "max_attempts" in condition

    def test_sql_retrying(self):
        """Test SQL condition for retrying jobs."""
        sm = TranscodingJobStateMachine()
        condition = sm.sql_retrying()

        assert "last_error IS NOT NULL" in condition
        assert "attempt_number <" in condition
        assert "claimed_at IS NULL" in condition

    def test_sql_claimable(self):
        """Test SQL condition for claimable jobs."""
        sm = TranscodingJobStateMachine()
        condition = sm.sql_claimable()

        # Claimable is same as unclaimed (expired claims handled separately)
        assert "claimed_at IS NULL" in condition
        assert "completed_at IS NULL" in condition


class TestTransitionValidation:
    """Tests for transition validation methods."""

    def test_can_claim_unclaimed_job(self):
        """Test can_claim returns True for unclaimed jobs."""
        job = JobRow(
            claimed_at=None,
            claim_expires_at=None,
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.can_claim(job) is True

    def test_can_claim_retrying_job(self):
        """Test can_claim returns True for retrying jobs."""
        job = JobRow(
            claimed_at=None,
            claim_expires_at=None,
            completed_at=None,
            last_error="Previous error",
            attempt_number=2,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.can_claim(job) is True

    def test_can_claim_already_claimed(self):
        """Test can_claim returns False for already claimed jobs."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=now,
            claim_expires_at=now + timedelta(minutes=30),
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.can_claim(job) is False

    def test_can_claim_completed(self):
        """Test can_claim returns False for completed jobs."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=None,
            claim_expires_at=None,
            completed_at=now,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.can_claim(job) is False

    def test_can_reclaim_expired(self):
        """Test can_reclaim returns True for expired claims."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=now - timedelta(minutes=35),
            claim_expires_at=now - timedelta(minutes=5),
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.can_reclaim(job, now=now) is True

    def test_can_reclaim_active_claim(self):
        """Test can_reclaim returns False for active claims."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=now - timedelta(minutes=5),
            claim_expires_at=now + timedelta(minutes=25),
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.can_reclaim(job, now=now) is False

    def test_can_complete_claimed(self):
        """Test can_complete returns True for claimed jobs."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=now - timedelta(minutes=5),
            claim_expires_at=now + timedelta(minutes=25),
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.can_complete(job, now=now) is True

    def test_can_complete_unclaimed(self):
        """Test can_complete returns False for unclaimed jobs."""
        job = JobRow(
            claimed_at=None,
            claim_expires_at=None,
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.can_complete(job) is False

    def test_can_fail_claimed(self):
        """Test can_fail returns True for claimed jobs."""
        now = datetime.now(timezone.utc)
        job = JobRow(
            claimed_at=now - timedelta(minutes=5),
            claim_expires_at=now + timedelta(minutes=25),
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )
        sm = TranscodingJobStateMachine()

        assert sm.can_fail(job, now=now) is True


class TestModuleSingleton:
    """Tests for module-level singleton."""

    def test_singleton_exists(self):
        """Test module provides a singleton instance."""
        assert job_state_machine is not None
        assert isinstance(job_state_machine, TranscodingJobStateMachine)

    def test_singleton_works(self):
        """Test singleton instance works correctly."""
        job = JobRow(
            claimed_at=None,
            claim_expires_at=None,
            completed_at=None,
            last_error=None,
            attempt_number=1,
            max_attempts=3,
        )

        assert job_state_machine.get_state(job) == JobState.UNCLAIMED


class TestJobStateEnum:
    """Tests for JobState enum."""

    def test_enum_values(self):
        """Test all expected states exist."""
        assert JobState.UNCLAIMED.value == "unclaimed"
        assert JobState.CLAIMED.value == "claimed"
        assert JobState.EXPIRED.value == "expired"
        assert JobState.COMPLETED.value == "completed"
        assert JobState.FAILED.value == "failed"
        assert JobState.RETRYING.value == "retrying"

    def test_enum_is_string(self):
        """Test JobState extends str for easy serialization."""
        assert isinstance(JobState.UNCLAIMED, str)
        # Using .value gives the string value
        assert JobState.UNCLAIMED.value == "unclaimed"
        # Can compare directly with strings
        assert JobState.UNCLAIMED == "unclaimed"
