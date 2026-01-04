"""
Transcoding Job State Machine - Explicit state management for transcoding jobs.

This module extracts the implicit state machine from transcoding_jobs table comments
into explicit, self-documenting code. States are derived from combinations of nullable
fields, but this abstraction makes the business logic clear.

Usage:
    from api.job_state import JobState, TranscodingJobStateMachine

    state_machine = TranscodingJobStateMachine()

    # Check current state of a job
    state = state_machine.get_state(job_row)

    # Get SQL conditions for queries
    unclaimed_condition = state_machine.sql_unclaimed()  # For WHERE clauses
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Union

import sqlalchemy as sa


class JobState(str, Enum):
    """
    Transcoding job states.

    States are derived from combinations of nullable fields:
    - claimed_at, claim_expires_at: Claim management
    - completed_at: Completion status
    - last_error, attempt_number, max_attempts: Error/retry tracking
    """

    UNCLAIMED = "unclaimed"
    """Job is available for any worker to claim.
    Condition: claimed_at IS NULL AND completed_at IS NULL"""

    CLAIMED = "claimed"
    """Worker has an active, valid claim on this job.
    Condition: claimed_at IS NOT NULL AND claim_expires_at > NOW() AND completed_at IS NULL"""

    EXPIRED = "expired"
    """Worker's claim has expired, job can be reclaimed.
    Condition: claimed_at IS NOT NULL AND claim_expires_at <= NOW() AND completed_at IS NULL"""

    COMPLETED = "completed"
    """Transcoding finished successfully.
    Condition: completed_at IS NOT NULL"""

    FAILED = "failed"
    """Permanently failed after all retry attempts.
    Condition: last_error IS NOT NULL AND attempt_number >= max_attempts"""

    RETRYING = "retrying"
    """Failed but available for retry.
    Condition: last_error IS NOT NULL AND attempt_number < max_attempts AND claimed_at IS NULL"""


@dataclass
class JobRow:
    """
    Represents a transcoding job row with fields needed for state determination.

    This can be constructed from a database row mapping.
    """

    claimed_at: Optional[datetime]
    claim_expires_at: Optional[datetime]
    completed_at: Optional[datetime]
    last_error: Optional[str]
    attempt_number: int
    max_attempts: int

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "JobRow":
        """Create JobRow from a database row mapping."""
        return cls(
            claimed_at=row.get("claimed_at"),
            claim_expires_at=row.get("claim_expires_at"),
            completed_at=row.get("completed_at"),
            last_error=row.get("last_error"),
            attempt_number=row.get("attempt_number", 1),
            max_attempts=row.get("max_attempts", 3),
        )


class TranscodingJobStateMachine:
    """
    Manages state determination and transitions for transcoding jobs.

    This class provides:
    1. State predicates: is_unclaimed(), is_claimed(), etc.
    2. State determination: get_state() returns the current JobState
    3. SQL conditions: sql_unclaimed(), sql_claimed(), etc. for query composition
    4. Transition validation: can_claim(), can_complete(), etc.

    Example:
        state_machine = TranscodingJobStateMachine()

        # Check if a job can be claimed
        if state_machine.is_unclaimed(job):
            # Claim it

        # Build a query for unclaimed jobs
        query = select(transcoding_jobs).where(state_machine.sql_unclaimed())
    """

    def __init__(self, jobs_table: Optional[sa.Table] = None):
        """
        Initialize the state machine.

        Args:
            jobs_table: SQLAlchemy Table for transcoding_jobs. If None, uses text-based
                        SQL conditions that work in raw SQL queries.
        """
        self._table = jobs_table

    # =========================================================================
    # State Predicates - Check if job is in a specific state
    # =========================================================================

    def is_unclaimed(self, job: Union[JobRow, Mapping[str, Any]]) -> bool:
        """
        Check if job is available for workers to claim.

        A job is unclaimed when:
        - No worker has claimed it (claimed_at IS NULL)
        - It hasn't been completed (completed_at IS NULL)
        """
        if isinstance(job, Mapping):
            job = JobRow.from_mapping(job)
        return job.claimed_at is None and job.completed_at is None

    def is_claimed(
        self, job: Union[JobRow, Mapping[str, Any]], now: Optional[datetime] = None
    ) -> bool:
        """
        Check if a worker has a valid, active claim on this job.

        A job is claimed when:
        - A worker has claimed it (claimed_at IS NOT NULL)
        - The claim hasn't expired (claim_expires_at > NOW())
        - It hasn't been completed (completed_at IS NULL)

        Args:
            job: The job row to check
            now: Current time for expiration check. Defaults to UTC now.
        """
        if isinstance(job, Mapping):
            job = JobRow.from_mapping(job)
        if now is None:
            now = datetime.now(timezone.utc)

        if job.claimed_at is None or job.completed_at is not None:
            return False
        if job.claim_expires_at is None:
            return False
        return job.claim_expires_at > now

    def is_expired(
        self, job: Union[JobRow, Mapping[str, Any]], now: Optional[datetime] = None
    ) -> bool:
        """
        Check if the worker's claim has expired and job can be reclaimed.

        A claim is expired when:
        - A worker had claimed it (claimed_at IS NOT NULL)
        - The claim has expired (claim_expires_at <= NOW())
        - It hasn't been completed (completed_at IS NULL)

        Args:
            job: The job row to check
            now: Current time for expiration check. Defaults to UTC now.
        """
        if isinstance(job, Mapping):
            job = JobRow.from_mapping(job)
        if now is None:
            now = datetime.now(timezone.utc)

        if job.claimed_at is None or job.completed_at is not None:
            return False
        if job.claim_expires_at is None:
            return False
        return job.claim_expires_at <= now

    def is_completed(self, job: Union[JobRow, Mapping[str, Any]]) -> bool:
        """
        Check if transcoding finished successfully.

        A job is completed when:
        - completed_at IS NOT NULL
        """
        if isinstance(job, Mapping):
            job = JobRow.from_mapping(job)
        return job.completed_at is not None

    def is_failed(self, job: Union[JobRow, Mapping[str, Any]]) -> bool:
        """
        Check if job permanently failed after all retry attempts.

        A job is failed when:
        - There was an error (last_error IS NOT NULL)
        - All attempts exhausted (attempt_number >= max_attempts)
        """
        if isinstance(job, Mapping):
            job = JobRow.from_mapping(job)
        return job.last_error is not None and job.attempt_number >= job.max_attempts

    def is_retrying(self, job: Union[JobRow, Mapping[str, Any]]) -> bool:
        """
        Check if job failed but is available for retry.

        A job is retrying when:
        - There was an error (last_error IS NOT NULL)
        - Attempts remain (attempt_number < max_attempts)
        - No active claim (claimed_at IS NULL)
        """
        if isinstance(job, Mapping):
            job = JobRow.from_mapping(job)
        return (
            job.last_error is not None
            and job.attempt_number < job.max_attempts
            and job.claimed_at is None
        )

    def get_state(
        self, job: Union[JobRow, Mapping[str, Any]], now: Optional[datetime] = None
    ) -> JobState:
        """
        Determine the current state of a job.

        Args:
            job: The job row to check
            now: Current time for expiration checks. Defaults to UTC now.

        Returns:
            The current JobState
        """
        if isinstance(job, Mapping):
            job = JobRow.from_mapping(job)
        if now is None:
            now = datetime.now(timezone.utc)

        # Check states in order of specificity
        if self.is_completed(job):
            return JobState.COMPLETED
        if self.is_failed(job):
            return JobState.FAILED
        if self.is_claimed(job, now):
            return JobState.CLAIMED
        if self.is_expired(job, now):
            return JobState.EXPIRED
        if self.is_retrying(job):
            return JobState.RETRYING
        if self.is_unclaimed(job):
            return JobState.UNCLAIMED

        # Should not reach here with valid data
        raise ValueError(f"Job is in an indeterminate state: {job}")

    # =========================================================================
    # SQL Conditions - For building database queries
    # =========================================================================

    def sql_unclaimed(self, table_alias: str = "tj") -> str:
        """
        SQL condition for unclaimed jobs.

        Returns a SQL fragment that can be used in WHERE clauses.

        Args:
            table_alias: Table alias for transcoding_jobs (default: "tj")

        Returns:
            SQL condition string
        """
        return f"{table_alias}.claimed_at IS NULL AND {table_alias}.completed_at IS NULL"

    def sql_claimed(self, table_alias: str = "tj", now_param: str = ":now") -> str:
        """
        SQL condition for actively claimed jobs.

        Args:
            table_alias: Table alias for transcoding_jobs (default: "tj")
            now_param: Parameter name for current timestamp (default: ":now")

        Returns:
            SQL condition string
        """
        return (
            f"{table_alias}.claimed_at IS NOT NULL "
            f"AND {table_alias}.claim_expires_at > {now_param} "
            f"AND {table_alias}.completed_at IS NULL"
        )

    def sql_expired(self, table_alias: str = "tj", now_param: str = ":now") -> str:
        """
        SQL condition for jobs with expired claims.

        Args:
            table_alias: Table alias for transcoding_jobs (default: "tj")
            now_param: Parameter name for current timestamp (default: ":now")

        Returns:
            SQL condition string
        """
        return (
            f"{table_alias}.claimed_at IS NOT NULL "
            f"AND {table_alias}.claim_expires_at <= {now_param} "
            f"AND {table_alias}.completed_at IS NULL"
        )

    def sql_completed(self, table_alias: str = "tj") -> str:
        """
        SQL condition for completed jobs.

        Args:
            table_alias: Table alias for transcoding_jobs (default: "tj")

        Returns:
            SQL condition string
        """
        return f"{table_alias}.completed_at IS NOT NULL"

    def sql_failed(self, table_alias: str = "tj") -> str:
        """
        SQL condition for permanently failed jobs.

        Args:
            table_alias: Table alias for transcoding_jobs (default: "tj")

        Returns:
            SQL condition string
        """
        return (
            f"{table_alias}.last_error IS NOT NULL "
            f"AND {table_alias}.attempt_number >= {table_alias}.max_attempts"
        )

    def sql_retrying(self, table_alias: str = "tj") -> str:
        """
        SQL condition for jobs awaiting retry.

        Args:
            table_alias: Table alias for transcoding_jobs (default: "tj")

        Returns:
            SQL condition string
        """
        return (
            f"{table_alias}.last_error IS NOT NULL "
            f"AND {table_alias}.attempt_number < {table_alias}.max_attempts "
            f"AND {table_alias}.claimed_at IS NULL"
        )

    def sql_claimable(self, table_alias: str = "tj") -> str:
        """
        SQL condition for jobs that can be claimed by a worker.

        A job is claimable if it's either unclaimed or has an expired claim.
        This is the condition used when workers look for work.

        Args:
            table_alias: Table alias for transcoding_jobs (default: "tj")

        Returns:
            SQL condition string
        """
        # Jobs are claimable when unclaimed and not completed
        # (Expired claims are handled separately by clearing claim fields first)
        return self.sql_unclaimed(table_alias)

    # =========================================================================
    # Transition Validation - Check if state transitions are valid
    # =========================================================================

    def can_claim(self, job: Union[JobRow, Mapping[str, Any]]) -> bool:
        """
        Check if a job can be claimed by a worker.

        A job can be claimed if:
        - It's unclaimed, OR
        - It's in retry state (failed but has attempts remaining)
        """
        if isinstance(job, Mapping):
            job = JobRow.from_mapping(job)
        return self.is_unclaimed(job) or self.is_retrying(job)

    def can_reclaim(
        self, job: Union[JobRow, Mapping[str, Any]], now: Optional[datetime] = None
    ) -> bool:
        """
        Check if a job can be reclaimed (claim expired).

        A job can be reclaimed if its claim has expired.
        """
        if isinstance(job, Mapping):
            job = JobRow.from_mapping(job)
        return self.is_expired(job, now)

    def can_complete(
        self, job: Union[JobRow, Mapping[str, Any]], now: Optional[datetime] = None
    ) -> bool:
        """
        Check if a job can be marked as complete.

        A job can be completed if:
        - It's currently claimed (active claim)
        - It's not already completed
        """
        if isinstance(job, Mapping):
            job = JobRow.from_mapping(job)
        return self.is_claimed(job, now)

    def can_fail(
        self, job: Union[JobRow, Mapping[str, Any]], now: Optional[datetime] = None
    ) -> bool:
        """
        Check if a job can be marked as failed.

        A job can fail if:
        - It's currently claimed
        - It's not already completed
        """
        if isinstance(job, Mapping):
            job = JobRow.from_mapping(job)
        return self.is_claimed(job, now)


# Module-level singleton for convenience
job_state_machine = TranscodingJobStateMachine()
