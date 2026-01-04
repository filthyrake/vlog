"""
Transcoding Job State Machine - Explicit state management for transcoding jobs.

This module extracts the implicit state machine from transcoding_jobs table comments
into explicit, self-documenting code. States are derived from combinations of nullable
fields, but this abstraction makes the business logic clear.

State Transition Diagram:
    UNCLAIMED ──> CLAIMED ──> COMPLETED
        │            │
        │            v
        │         EXPIRED ──> (reclaim clears claim fields)
        │            │
        v            v
    RETRYING ──> FAILED (when max attempts reached)

Usage:
    from api.job_state import JobState, TranscodingJobStateMachine

    state_machine = TranscodingJobStateMachine()

    # Check current state of a job
    state = state_machine.get_state(job_row)

    # Get SQL conditions for queries
    unclaimed_condition = state_machine.sql_unclaimed()  # For WHERE clauses

Note: State checks are point-in-time and advisory. For safe state transitions
in a distributed environment, use database-level locking (e.g., FOR UPDATE).
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Union

logger = logging.getLogger(__name__)

# Pattern for valid SQL identifiers (table aliases, column names)
_SAFE_SQL_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Pattern for valid SQL parameter names (e.g., :now, :current_time)
_SAFE_SQL_PARAM = re.compile(r"^:[a-zA-Z_][a-zA-Z0-9_]*$")


class JobState(str, Enum):
    """
    Transcoding job states (mutually exclusive).

    State Transition Flow:
        UNCLAIMED -> CLAIMED -> COMPLETED
                  |
                  v
               EXPIRED -> (back to UNCLAIMED via reclaim)
                  |
                  v
               FAILED (if max attempts reached)
                  |
                  v
               RETRYING -> (back to claimable for retry)

    States are derived from combinations of nullable database fields.
    Use TranscodingJobStateMachine.get_state() to determine current state.
    """

    UNCLAIMED = "unclaimed"
    """Job is available for any worker to claim.
    Condition: claimed_at IS NULL AND completed_at IS NULL AND (last_error IS NULL OR has retries)"""

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
    Condition: completed_at IS NULL AND last_error IS NOT NULL AND attempt_number >= max_attempts"""

    RETRYING = "retrying"
    """Failed but available for retry.
    Condition: completed_at IS NULL AND last_error IS NOT NULL AND attempt_number < max_attempts AND claimed_at IS NULL"""


def _ensure_utc_datetime(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Normalize datetime to UTC timezone.

    Args:
        dt: A datetime that may be naive or timezone-aware

    Returns:
        UTC-aware datetime, or None if input is None
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Naive datetime - assume UTC and log warning
        logger.warning(f"Naive datetime detected, assuming UTC: {dt}")
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class JobRow:
    """
    State-relevant fields from a transcoding job.

    Contains only the fields needed to determine job state.
    Can be constructed from a database row mapping.

    All datetime fields are normalized to UTC timezone.
    """

    claimed_at: Optional[datetime]
    claim_expires_at: Optional[datetime]
    completed_at: Optional[datetime]
    last_error: Optional[str]
    attempt_number: int
    max_attempts: int

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "JobRow":
        """
        Create JobRow from a database row mapping.

        Normalizes datetime fields to UTC and validates numeric fields.
        """
        # Normalize datetimes to UTC
        claimed_at = _ensure_utc_datetime(row.get("claimed_at"))
        claim_expires_at = _ensure_utc_datetime(row.get("claim_expires_at"))
        completed_at = _ensure_utc_datetime(row.get("completed_at"))

        # Get numeric fields with defaults, ensure minimum values
        attempt_number = row.get("attempt_number", 1)
        max_attempts = row.get("max_attempts", 3)

        # Ensure valid ranges (minimum 1 for both)
        if attempt_number is None or attempt_number < 1:
            attempt_number = 1
        if max_attempts is None or max_attempts < 1:
            max_attempts = 3

        return cls(
            claimed_at=claimed_at,
            claim_expires_at=claim_expires_at,
            completed_at=completed_at,
            last_error=row.get("last_error"),
            attempt_number=attempt_number,
            max_attempts=max_attempts,
        )


class TranscodingJobStateMachine:
    """
    Manages state determination and transitions for transcoding jobs.

    This class provides:
    1. State predicates: is_unclaimed(), is_claimed(), etc.
    2. State determination: get_state() returns the current JobState
    3. SQL conditions: sql_unclaimed(), sql_claimed(), etc. for query composition
    4. Transition validation: can_claim(), can_complete(), etc.

    Thread Safety:
        This class is stateless and thread-safe. All methods are pure functions.

    Distributed Safety:
        State checks are point-in-time. For safe transitions in distributed
        environments, use database-level locking (e.g., FOR UPDATE SKIP LOCKED).

    Example:
        state_machine = TranscodingJobStateMachine()

        # Check if a job can be claimed
        if state_machine.is_unclaimed(job):
            # Claim it (with proper DB locking)

        # Build a query for unclaimed jobs
        condition = state_machine.sql_unclaimed()
        query = f"SELECT * FROM transcoding_jobs tj WHERE {condition}"
    """

    def __init__(self) -> None:
        """Initialize the state machine."""
        pass

    # =========================================================================
    # Internal Helpers
    # =========================================================================

    def _normalize_job(self, job: Union[JobRow, Mapping[str, Any]]) -> JobRow:
        """Convert job to JobRow if needed."""
        if isinstance(job, Mapping):
            return JobRow.from_mapping(job)
        return job

    def _validate_sql_identifier(self, value: str, param_name: str) -> None:
        """
        Validate that a string is a safe SQL identifier.

        Args:
            value: The identifier to validate
            param_name: Name of the parameter (for error messages)

        Raises:
            ValueError: If the identifier is not safe
        """
        if not _SAFE_SQL_IDENTIFIER.match(value):
            raise ValueError(
                f"Invalid SQL identifier for {param_name}: '{value}'. "
                f"Must match pattern: [a-zA-Z_][a-zA-Z0-9_]*"
            )

    def _validate_sql_param(self, value: str, param_name: str) -> None:
        """
        Validate that a string is a safe SQL parameter name.

        Args:
            value: The parameter name to validate (e.g., ":now")
            param_name: Name of the parameter (for error messages)

        Raises:
            ValueError: If the parameter name is not safe
        """
        if not _SAFE_SQL_PARAM.match(value):
            raise ValueError(
                f"Invalid SQL parameter for {param_name}: '{value}'. "
                f"Must match pattern: :[a-zA-Z_][a-zA-Z0-9_]*"
            )

    # =========================================================================
    # State Predicates - Check if job is in a specific state
    # =========================================================================

    def is_unclaimed(self, job: Union[JobRow, Mapping[str, Any]]) -> bool:
        """
        Check if job is available for workers to claim.

        A job is unclaimed when it has no active claim, is not completed,
        and is not in a retry/failed state due to errors.
        """
        job = self._normalize_job(job)
        return (
            job.claimed_at is None
            and job.completed_at is None
            and job.last_error is None
        )

    def is_claimed(
        self, job: Union[JobRow, Mapping[str, Any]], current_time: Optional[datetime] = None
    ) -> bool:
        """
        Check if a worker has a valid, active claim on this job.

        A job is claimed when:
        - A worker has claimed it (claimed_at IS NOT NULL)
        - The claim hasn't expired (claim_expires_at > current_time)
        - It hasn't been completed (completed_at IS NULL)

        Args:
            job: The job row to check
            current_time: Time to use for expiration check. Defaults to UTC now.
        """
        job = self._normalize_job(job)
        if current_time is None:
            current_time = datetime.now(timezone.utc)

        return (
            job.claimed_at is not None
            and job.completed_at is None
            and job.claim_expires_at is not None
            and job.claim_expires_at > current_time
        )

    def is_expired(
        self, job: Union[JobRow, Mapping[str, Any]], current_time: Optional[datetime] = None
    ) -> bool:
        """
        Check if the worker's claim has expired and job can be reclaimed.

        A claim is expired when:
        - A worker had claimed it (claimed_at IS NOT NULL)
        - The claim has expired (claim_expires_at <= current_time)
        - It hasn't been completed (completed_at IS NULL)

        Args:
            job: The job row to check
            current_time: Time to use for expiration check. Defaults to UTC now.
        """
        job = self._normalize_job(job)
        if current_time is None:
            current_time = datetime.now(timezone.utc)

        return (
            job.claimed_at is not None
            and job.completed_at is None
            and job.claim_expires_at is not None
            and job.claim_expires_at <= current_time
        )

    def is_completed(self, job: Union[JobRow, Mapping[str, Any]]) -> bool:
        """
        Check if transcoding finished successfully.

        A job is completed when completed_at IS NOT NULL.
        """
        job = self._normalize_job(job)
        return job.completed_at is not None

    def is_failed(self, job: Union[JobRow, Mapping[str, Any]]) -> bool:
        """
        Check if job permanently failed after all retry attempts.

        A job is failed when:
        - It's not completed (completed_at IS NULL)
        - There was an error (last_error IS NOT NULL)
        - All attempts exhausted (attempt_number >= max_attempts)
        """
        job = self._normalize_job(job)
        return (
            job.completed_at is None
            and job.last_error is not None
            and job.attempt_number >= job.max_attempts
        )

    def is_retrying(self, job: Union[JobRow, Mapping[str, Any]]) -> bool:
        """
        Check if job failed but is available for retry.

        A job is retrying when:
        - It's not completed (completed_at IS NULL)
        - There was an error (last_error IS NOT NULL)
        - Attempts remain (attempt_number < max_attempts)
        - No active claim (claimed_at IS NULL)
        """
        job = self._normalize_job(job)
        return (
            job.completed_at is None
            and job.last_error is not None
            and job.attempt_number < job.max_attempts
            and job.claimed_at is None
        )

    def get_state(
        self, job: Union[JobRow, Mapping[str, Any]], current_time: Optional[datetime] = None
    ) -> JobState:
        """
        Determine the current state of a job.

        Args:
            job: The job row to check
            current_time: Time to use for expiration checks. Defaults to UTC now.

        Returns:
            The current JobState

        Raises:
            ValueError: If the job data is invalid and state cannot be determined.
                        This indicates database constraint violations or corruption.
        """
        # Convert once at entry point for efficiency
        job = self._normalize_job(job)
        if current_time is None:
            current_time = datetime.now(timezone.utc)

        # Check states in order of specificity
        # Terminal states first
        if self.is_completed(job):
            return JobState.COMPLETED
        if self.is_failed(job):
            return JobState.FAILED

        # Time-sensitive states
        if self.is_claimed(job, current_time):
            return JobState.CLAIMED
        if self.is_expired(job, current_time):
            return JobState.EXPIRED

        # Retry state
        if self.is_retrying(job):
            return JobState.RETRYING

        # Default unclaimed state
        if self.is_unclaimed(job):
            return JobState.UNCLAIMED

        # If we reach here, the job data is invalid
        # This can happen if claimed_at is set but claim_expires_at is NULL
        logger.error(
            f"Job in indeterminate state - possible data corruption: "
            f"claimed_at={job.claimed_at}, claim_expires_at={job.claim_expires_at}, "
            f"completed_at={job.completed_at}, last_error={job.last_error is not None}, "
            f"attempt_number={job.attempt_number}, max_attempts={job.max_attempts}"
        )
        raise ValueError("Job is in an indeterminate state - data may be corrupted")

    # =========================================================================
    # SQL Conditions - For building database queries
    # =========================================================================

    def sql_unclaimed(self, table_alias: str = "tj") -> str:
        """
        SQL condition for unclaimed jobs (no error, no claim, not complete).

        Args:
            table_alias: Table alias for transcoding_jobs (default: "tj")

        Returns:
            SQL condition string

        Raises:
            ValueError: If table_alias is not a valid SQL identifier
        """
        self._validate_sql_identifier(table_alias, "table_alias")
        return (
            f"{table_alias}.claimed_at IS NULL "
            f"AND {table_alias}.completed_at IS NULL "
            f"AND {table_alias}.last_error IS NULL"
        )

    def sql_claimed(self, table_alias: str = "tj", now_param: str = ":now") -> str:
        """
        SQL condition for actively claimed jobs.

        Args:
            table_alias: Table alias for transcoding_jobs (default: "tj")
            now_param: Parameter name for current timestamp (default: ":now")

        Returns:
            SQL condition string

        Raises:
            ValueError: If table_alias or now_param is not valid
        """
        self._validate_sql_identifier(table_alias, "table_alias")
        self._validate_sql_param(now_param, "now_param")
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

        Raises:
            ValueError: If table_alias or now_param is not valid
        """
        self._validate_sql_identifier(table_alias, "table_alias")
        self._validate_sql_param(now_param, "now_param")
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

        Raises:
            ValueError: If table_alias is not a valid SQL identifier
        """
        self._validate_sql_identifier(table_alias, "table_alias")
        return f"{table_alias}.completed_at IS NOT NULL"

    def sql_failed(self, table_alias: str = "tj") -> str:
        """
        SQL condition for permanently failed jobs.

        Args:
            table_alias: Table alias for transcoding_jobs (default: "tj")

        Returns:
            SQL condition string

        Raises:
            ValueError: If table_alias is not a valid SQL identifier
        """
        self._validate_sql_identifier(table_alias, "table_alias")
        return (
            f"{table_alias}.completed_at IS NULL "
            f"AND {table_alias}.last_error IS NOT NULL "
            f"AND {table_alias}.attempt_number >= {table_alias}.max_attempts"
        )

    def sql_retrying(self, table_alias: str = "tj") -> str:
        """
        SQL condition for jobs awaiting retry.

        Args:
            table_alias: Table alias for transcoding_jobs (default: "tj")

        Returns:
            SQL condition string

        Raises:
            ValueError: If table_alias is not a valid SQL identifier
        """
        self._validate_sql_identifier(table_alias, "table_alias")
        return (
            f"{table_alias}.completed_at IS NULL "
            f"AND {table_alias}.last_error IS NOT NULL "
            f"AND {table_alias}.attempt_number < {table_alias}.max_attempts "
            f"AND {table_alias}.claimed_at IS NULL"
        )

    def sql_claimable(self, table_alias: str = "tj") -> str:
        """
        SQL condition for jobs that can be claimed by a worker.

        A job is claimable if it's either:
        - Unclaimed (no claim, no error, not complete), OR
        - Retrying (has error but attempts remain and no active claim)

        Note: Expired claims should be cleared before using this condition.

        Args:
            table_alias: Table alias for transcoding_jobs (default: "tj")

        Returns:
            SQL condition string

        Raises:
            ValueError: If table_alias is not a valid SQL identifier
        """
        self._validate_sql_identifier(table_alias, "table_alias")
        unclaimed = self.sql_unclaimed(table_alias)
        retrying = self.sql_retrying(table_alias)
        return f"(({unclaimed}) OR ({retrying}))"

    # =========================================================================
    # Transition Validation - Check if state transitions are valid
    # =========================================================================

    def can_claim(
        self, job: Union[JobRow, Mapping[str, Any]], current_time: Optional[datetime] = None
    ) -> bool:
        """
        Check if a job can be claimed by a worker.

        A job can be claimed if:
        - It's unclaimed, OR
        - It's in retry state (failed but has attempts remaining)

        Args:
            job: The job to check
            current_time: Current time (unused, but accepted for API consistency)
        """
        job = self._normalize_job(job)
        return self.is_unclaimed(job) or self.is_retrying(job)

    def can_reclaim(
        self, job: Union[JobRow, Mapping[str, Any]], current_time: Optional[datetime] = None
    ) -> bool:
        """
        Check if a job can be reclaimed (claim expired).

        A job can be reclaimed if its claim has expired.
        """
        job = self._normalize_job(job)
        return self.is_expired(job, current_time)

    def can_complete(
        self, job: Union[JobRow, Mapping[str, Any]], current_time: Optional[datetime] = None
    ) -> bool:
        """
        Check if a job can be marked as complete.

        A job can be completed if it's currently claimed (active claim).

        Note: This only validates state, not worker identity. The caller
        should also verify that the requesting worker holds the claim.
        """
        job = self._normalize_job(job)
        return self.is_claimed(job, current_time)

    def can_fail(
        self, job: Union[JobRow, Mapping[str, Any]], current_time: Optional[datetime] = None
    ) -> bool:
        """
        Check if a job can be marked as failed.

        A job can fail if it's currently claimed.

        Note: This only validates state, not worker identity. The caller
        should also verify that the requesting worker holds the claim.
        """
        job = self._normalize_job(job)
        return self.is_claimed(job, current_time)


# Module-level singleton for convenience (thread-safe, stateless)
job_state_machine = TranscodingJobStateMachine()
