"""Taxonomy enums for time assurance domains and confidence."""

from enum import StrEnum


class TimeDomain(StrEnum):
    """Primary and general time-assurance domains."""

    Y2036 = "y2036"
    Y2038 = "y2038"
    Y2100 = "y2100"
    Y2106 = "y2106"
    EPOCH = "epoch"
    REPRESENTATION = "representation"
    SIGNEDNESS = "signedness"
    DATE_RANGE = "date_range"
    CALENDAR = "calendar"
    LEAP_YEAR = "leap_year"
    LEAP_SECOND = "leap_second"
    UTC = "utc"
    TAI = "tai"
    GPS_TIME = "gps_time"
    MONOTONIC = "monotonic"
    RELATIVE_VS_ABSOLUTE = "relative_vs_absolute"
    SERIALIZATION = "serialization"
    PERSISTENCE = "persistence"
    SYNCHRONIZATION = "synchronization"
    ARCHIVAL = "archival"
    CERTIFICATE_VALIDITY = "certificate_validity"
    SCHEDULING = "scheduling"
    MIGRATION = "migration"
    ROLLOVER = "rollover"
    MISSING_DOCUMENTATION = "missing_documentation"
    OTHER = "other"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
