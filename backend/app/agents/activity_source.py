"""Activity sources.

A source turns an underlying signal (simulated here, real desktop/browser
capture later) into ``ActivityEventCreate`` records. Sources never touch
storage — the activity service persists whatever a source produces, so
``SimulatedActivitySource`` can be swapped for ``MacOSActivitySource`` or
``BrowserActivitySource`` without changing the API or the database layer.

Privacy: sources record high-level actions only (open, read, download,
update, send). No keystrokes, clipboard, passwords or message bodies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Protocol, Sequence

from app.schemas.activity import ActivityCategory, ActivityEventCreate

DEFAULT_REPETITION_GAP_SECONDS = 120


@dataclass(frozen=True)
class SequenceStep:
    """One step of a predefined workflow-like activity sequence."""

    offset: int
    application: str
    category: ActivityCategory
    action: str
    description: str
    metadata: Dict[str, Any] = field(default_factory=dict)


SEQUENCES: Dict[str, List[SequenceStep]] = {
    "customer_request": [
        SequenceStep(
            offset=0,
            application="Gmail",
            category="communication",
            action="open_email",
            description="Opened customer request email",
            metadata={"subject": "Customer Request - ABC Industries"},
        ),
        SequenceStep(
            offset=23,
            application="Gmail",
            category="communication",
            action="read_email",
            description="Read customer request",
            metadata={"subject": "Customer Request - ABC Industries"},
        ),
        SequenceStep(
            offset=51,
            application="Gmail",
            category="file",
            action="download_attachment",
            description="Downloaded customer_request.pdf",
            metadata={"file": "customer_request.pdf", "size_kb": 248},
        ),
        SequenceStep(
            offset=74,
            application="CRM",
            category="crm",
            action="find_customer",
            description="Found customer ABC Industries",
            metadata={"customer": "ABC Industries"},
        ),
        SequenceStep(
            offset=130,
            application="CRM",
            category="crm",
            action="update_customer",
            description="Updated customer record",
            metadata={"customer": "ABC Industries", "fields_updated": 3},
        ),
        SequenceStep(
            offset=146,
            application="Slack",
            category="communication",
            action="send_notification",
            description="Sent notification to support team",
            metadata={"channel": "#support"},
        ),
    ],
    "document_processing": [
        SequenceStep(
            offset=0,
            application="Gmail",
            category="communication",
            action="open_email",
            description="Opened document request email",
            metadata={"subject": "Q3 Report needed"},
        ),
        SequenceStep(
            offset=18,
            application="Gmail",
            category="file",
            action="download_document",
            description="Downloaded Q3_Report.xlsx",
            metadata={"file": "Q3_Report.xlsx"},
        ),
        SequenceStep(
            offset=35,
            application="Finder",
            category="file",
            action="open_document",
            description="Opened Q3_Report.xlsx",
            metadata={"path": "~/Downloads/Q3_Report.xlsx"},
        ),
        SequenceStep(
            offset=72,
            application="Excel",
            category="application",
            action="update_spreadsheet",
            description="Updated spreadsheet figures",
            metadata={"file": "Q3_Report.xlsx", "rows_updated": 12},
        ),
        SequenceStep(
            offset=95,
            application="Excel",
            category="file",
            action="save_file",
            description="Saved spreadsheet",
            metadata={"file": "Q3_Report.xlsx"},
        ),
    ],
    "meeting_followup": [
        SequenceStep(
            offset=0,
            application="Calendar",
            category="application",
            action="open_meeting",
            description="Opened weekly sync meeting",
            metadata={"meeting": "Weekly Sync"},
        ),
        SequenceStep(
            offset=12,
            application="Calendar",
            category="application",
            action="read_meeting_details",
            description="Read meeting details",
            metadata={"meeting": "Weekly Sync"},
        ),
        SequenceStep(
            offset=41,
            application="Gmail",
            category="communication",
            action="compose_email",
            description="Composed follow-up email",
            metadata={"subject": "Follow-up: Weekly Sync"},
        ),
        SequenceStep(
            offset=66,
            application="Gmail",
            category="file",
            action="attach_document",
            description="Attached meeting_notes.docx",
            metadata={"file": "meeting_notes.docx"},
        ),
        SequenceStep(
            offset=88,
            application="Gmail",
            category="communication",
            action="send_email",
            description="Sent follow-up email",
            metadata={"subject": "Follow-up: Weekly Sync"},
        ),
    ],
}


class ActivitySource(Protocol):
    """Anything that can produce structured activity events."""

    @property
    def workflows(self) -> Sequence[str]: ...

    def generate(
        self,
        workflow: str,
        session_id: str,
        end_at: datetime,
    ) -> List[ActivityEventCreate]: ...


class UnknownWorkflowError(ValueError):
    def __init__(self, workflow: str) -> None:
        super().__init__(f"Unknown workflow '{workflow}'")
        self.workflow = workflow


class SimulatedActivitySource:
    """Generates realistic, timestamped activity for predefined workflows."""

    @property
    def workflows(self) -> Sequence[str]:
        return tuple(SEQUENCES.keys())

    def has_workflow(self, workflow: str) -> bool:
        return workflow in SEQUENCES

    def generate(
        self,
        workflow: str,
        session_id: str,
        end_at: datetime,
        start_at: Optional[datetime] = None,
    ) -> List[ActivityEventCreate]:
        """Build one complete sequence for ``workflow``.

        Events are spread realistically: ``start_at`` defaults to a point that
        makes the final step land on ``end_at``, so generated activity always
        sits in the recent past instead of the future.
        """
        if workflow not in SEQUENCES:
            raise UnknownWorkflowError(workflow)

        steps = SEQUENCES[workflow]
        span = max(step.offset for step in steps)
        if start_at is None:
            start_at = end_at - timedelta(seconds=span)

        events: List[ActivityEventCreate] = []
        for step in steps:
            events.append(
                ActivityEventCreate(
                    timestamp=start_at + timedelta(seconds=step.offset),
                    application=step.application,
                    category=step.category,
                    action=step.action,
                    description=step.description,
                    metadata=dict(step.metadata),
                    session_id=session_id,
                )
            )
        return events
