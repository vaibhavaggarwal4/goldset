from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from evalkit.errors import UserFacingError
from evalkit.models import EvalTarget, EvaluationResult, Finding, ReviewSignal, Rubric


class EvalStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.migrate()

    def migrate(self) -> None:
        self.conn.executescript(
            """
            create table if not exists runs (
              id text primary key,
              suite_name text not null,
              rubric_name text not null,
              rubric_version text not null,
              provider text not null,
              model text,
              input_path text,
              created_at text not null
            );

            create table if not exists case_results (
              run_id text not null,
              case_id text not null,
              input_text text,
              artifact_type text,
              artifact_content text,
              metadata_json text,
              fields_json text,
              passed integer,
              primary key (run_id, case_id)
            );

            create table if not exists dimension_results (
              id integer primary key autoincrement,
              run_id text not null,
              case_id text not null,
              dimension_name text not null,
              evaluator text not null,
              passed integer,
              score real,
              rationale text,
              details_json text,
              requires_human_review integer default 0
            );

            create table if not exists human_reviews (
              id integer primary key autoincrement,
              run_id text not null,
              case_id text not null,
              dimension_name text not null,
              reviewer text not null,
              passed integer not null,
              score real,
              notes text,
              created_at text not null
            );

            create table if not exists review_signals (
              id integer primary key autoincrement,
              run_id text not null,
              case_id text not null,
              dimension_name text not null,
              machine_passed integer,
              human_passed integer not null,
              reviewer text not null,
              notes text,
              correction text,
              failure_reason text,
              signal_type text not null,
              created_at text not null
            );

            create table if not exists findings (
              id integer primary key autoincrement,
              run_id text not null,
              title text not null,
              dimension_name text not null,
              failure_reason text not null,
              case_count integer not null,
              signal_ids_json text not null,
              status text not null,
              created_at text not null
            );

            create table if not exists eval_targets (
              id integer primary key autoincrement,
              run_id text not null,
              finding_id integer not null,
              title text not null,
              success_criteria_json text not null,
              regression_cases_json text not null,
              owner text not null,
              created_at text not null
            );
            """
        )
        self._ensure_column("human_reviews", "correction", "text")
        self._ensure_column("human_reviews", "failure_reason", "text")
        self._ensure_column("human_reviews", "rubric_issue", "integer default 0")
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.conn.execute(f"pragma table_info({table})")}
        if column not in columns:
            self.conn.execute(f"alter table {table} add column {column} {definition}")

    def create_run(
        self,
        *,
        suite_name: str,
        rubric: Rubric,
        provider: str,
        model: str | None,
        input_path: str,
    ) -> str:
        run_id = str(uuid.uuid4())
        self.conn.execute(
            """
            insert into runs (id, suite_name, rubric_name, rubric_version, provider, model, input_path, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                suite_name,
                rubric.name,
                rubric.version,
                provider,
                model,
                input_path,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()
        return run_id

    def save_results(self, run_id: str, results: list[EvaluationResult]) -> None:
        for result in results:
            self.conn.execute(
                """
                insert or replace into case_results
                (run_id, case_id, input_text, artifact_type, artifact_content, metadata_json, fields_json, passed)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    result.case.case_id,
                    result.case.input_text,
                    result.case.artifact.artifact_type,
                    result.case.artifact.content,
                    json.dumps(result.case.metadata),
                    json.dumps(result.case.artifact.fields),
                    _to_int(result.passed),
                ),
            )
            for dimension in result.dimension_results:
                self.conn.execute(
                    """
                    insert into dimension_results
                    (run_id, case_id, dimension_name, evaluator, passed, score, rationale, details_json, requires_human_review)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        dimension.case_id,
                        dimension.dimension_name,
                        dimension.evaluator,
                        _to_int(dimension.passed),
                        dimension.score,
                        dimension.rationale,
                        json.dumps(dimension.details),
                        int(dimension.requires_human_review),
                    ),
                )
        self.conn.commit()

    def latest_run_id(self) -> str:
        row = self.conn.execute("select id from runs order by created_at desc limit 1").fetchone()
        if not row:
            raise UserFacingError(
                "No evaluation runs found in this database.\n"
                "Fix: run evalkit run first, or pass --db with the path to the database you used."
            )
        return str(row["id"])

    def run(self, run_id: str) -> sqlite3.Row:
        row = self.conn.execute("select * from runs where id = ?", (run_id,)).fetchone()
        if not row:
            raise UserFacingError(
                f"Run not found: {run_id}\n"
                "Fix: use --run-id latest, or check the run ID printed by evalkit run."
            )
        return row

    def case_rows(self, run_id: str) -> list[sqlite3.Row]:
        return list(self.conn.execute("select * from case_results where run_id = ? order by case_id", (run_id,)))

    def dimension_rows(self, run_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "select * from dimension_results where run_id = ? order by case_id, dimension_name",
                (run_id,),
            )
        )

    def human_review_rows(self, run_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "select * from human_reviews where run_id = ? order by created_at desc",
                (run_id,),
            )
        )

    def save_human_review(
        self,
        *,
        run_id: str,
        case_id: str,
        dimension_name: str,
        reviewer: str,
        passed: bool,
        score: float | None,
        notes: str,
        correction: str = "",
        failure_reason: str = "",
        rubric_issue: bool = False,
    ) -> None:
        self.conn.execute(
            """
            insert into human_reviews
            (run_id, case_id, dimension_name, reviewer, passed, score, notes, correction, failure_reason, rubric_issue, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                case_id,
                dimension_name,
                reviewer,
                int(passed),
                score,
                notes,
                correction,
                failure_reason,
                int(rubric_issue),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()

    def save_review_signal(self, signal: ReviewSignal) -> int:
        cursor = self.conn.execute(
            """
            insert into review_signals
            (run_id, case_id, dimension_name, machine_passed, human_passed, reviewer, notes, correction, failure_reason, signal_type, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.run_id,
                signal.case_id,
                signal.dimension_name,
                _to_int(signal.machine_passed),
                int(signal.human_passed),
                signal.reviewer,
                signal.notes,
                signal.correction,
                signal.failure_reason,
                signal.signal_type,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def clear_review_signals(self, run_id: str) -> None:
        self.conn.execute("delete from review_signals where run_id = ?", (run_id,))
        self.conn.commit()

    def review_signal_rows(self, run_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "select * from review_signals where run_id = ? order by dimension_name, case_id",
                (run_id,),
            )
        )

    def save_finding(self, run_id: str, finding: Finding) -> int:
        cursor = self.conn.execute(
            """
            insert into findings
            (run_id, title, dimension_name, failure_reason, case_count, signal_ids_json, status, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                finding.title,
                finding.dimension_name,
                finding.failure_reason,
                finding.case_count,
                json.dumps(finding.signal_ids),
                finding.status,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def clear_findings(self, run_id: str) -> None:
        self.conn.execute("delete from findings where run_id = ?", (run_id,))
        self.conn.commit()

    def finding_rows(self, run_id: str) -> list[sqlite3.Row]:
        return list(self.conn.execute("select * from findings where run_id = ? order by case_count desc, id", (run_id,)))

    def finding(self, finding_id: int) -> sqlite3.Row:
        row = self.conn.execute("select * from findings where id = ?", (finding_id,)).fetchone()
        if not row:
            raise UserFacingError(
                f"Finding not found: {finding_id}\n"
                "Fix: run evalkit findings first, then use one of the IDs it prints."
            )
        return row

    def save_eval_target(self, run_id: str, target: EvalTarget) -> int:
        cursor = self.conn.execute(
            """
            insert into eval_targets
            (run_id, finding_id, title, success_criteria_json, regression_cases_json, owner, created_at)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                target.finding_id,
                target.title,
                json.dumps(target.success_criteria),
                json.dumps(target.regression_cases),
                target.owner,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def eval_target_rows(self, run_id: str) -> list[sqlite3.Row]:
        return list(self.conn.execute("select * from eval_targets where run_id = ? order by id", (run_id,)))


def _to_int(value: bool | None) -> int | None:
    if value is None:
        return None
    return int(value)
