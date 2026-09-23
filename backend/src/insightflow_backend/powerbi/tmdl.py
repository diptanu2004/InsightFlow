"""BIModel -> the text files of a Power BI project (PBIP/TMDL).

Deliberately a dumb serializer: every decision was already made in build.py, against types this repo
owns and tests without Power BI. What is left here is formatting, which is the part most likely to
need a small correction the first time Desktop opens the output -- and the part where a correction
costs nothing, because it changes no numbers.

Format notes that are easy to get wrong and expensive to debug:
  - TMDL indents with TABS. Spaces are a parse error.
  - A property's value may continue onto following lines as long as they are indented deeper, which
    is how multi-line DAX and M are written below.
  - Power Query has no notion of "the folder this project is in", so the CSV partitions read from a
    `DataFolder` parameter instead of a baked-in absolute path. The export README tells the reader to
    point it at the unzipped `data` folder; it is the one manual step.
"""
import json
import uuid
from datetime import date

from insightflow_backend.powerbi.ir import BIDateTable, BIMeasure, BIModel, BIRelationship, BITable

TAB = "\t"

# BIColumn.data_type -> the M type a Csv.Document partition casts to. Without an explicit cast every
# column arrives as text and a SUM measure silently returns blank.
_M_TYPES = {
    "string": "type text",
    "int64": "Int64.Type",
    "double": "type number",
    "dateTime": "type datetime",
    "boolean": "type logical",
}

_FORMAT_BY_TYPE = {
    "int64": "0",
    "double": "#,0.00",
    "dateTime": "General Date",
}


def _escape_m(value: str) -> str:
    return value.replace('"', '""')


def _quote_tmdl(name: str) -> str:
    """TMDL object names need quoting when they are not bare identifiers. Entity names come from
    upload filenames, so spaces and punctuation are routine, not exotic."""
    if name and all(c.isalnum() or c == "_" for c in name) and not name[0].isdigit():
        return name
    return "'" + name.replace("'", "''") + "'"


def _indented_block(text: str, depth: int) -> list[str]:
    return [TAB * depth + line if line.strip() else "" for line in text.split("\n")]


def _lineage_tag(*parts: str) -> str:
    """A stable per-object id. Power BI wants a lineageTag on each object; deriving it from the
    object's own path (rather than uuid4) keeps re-exports of an unchanged dataset byte-identical,
    so a person tracking the export in git sees real changes only."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "insightflow:powerbi:" + "/".join(parts)))


def _column_lines(table: BITable, depth: int = 1) -> list[str]:
    lines: list[str] = []
    for column in table.columns:
        if column.canonical_field:
            # The mapping the review UI shows, carried into Desktop so the two agree about what a
            # column means -- including that it is a guess with a confidence, not a fact. A `///`
            # block is TMDL's description syntax and must PRECEDE the object it describes.
            confidence = f" ({column.confidence:.0%} confidence)" if column.confidence is not None else ""
            lines.append(f"{TAB * depth}/// InsightFlow mapping: {column.canonical_field}{confidence}")
        lines.append(f"{TAB * depth}column {_quote_tmdl(column.name)}")
        lines.append(f"{TAB * (depth + 1)}dataType: {column.data_type}")
        if column.data_type in _FORMAT_BY_TYPE:
            lines.append(f"{TAB * (depth + 1)}formatString: {_FORMAT_BY_TYPE[column.data_type]}")
        lines.append(f"{TAB * (depth + 1)}lineageTag: {_lineage_tag(table.name, column.name)}")
        lines.append(f"{TAB * (depth + 1)}summarizeBy: none")
        lines.append(f"{TAB * (depth + 1)}sourceColumn: {column.name}")
        lines.append("")
    return lines


def _partition_lines(table: BITable, delimiter: str, depth: int = 1) -> list[str]:
    columns = ", ".join(
        f'{{"{_escape_m(c.name)}", {_M_TYPES[c.data_type]}}}' for c in table.columns
    )
    selected = ", ".join(f'"{_escape_m(c.name)}"' for c in table.columns)
    m_query = (
        "let\n"
        f'    Source = Csv.Document(File.Contents(DataFolder & "{_escape_m(table.source_file)}.csv"), '
        f'[Delimiter="{_escape_m(delimiter)}", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),\n'
        "    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),\n"
        f"    Typed = Table.TransformColumnTypes(Promoted, {{{columns}}}),\n"
        f"    Selected = Table.SelectColumns(Typed, {{{selected}}})\n"
        "in\n"
        "    Selected"
    )
    lines = [f"{TAB * depth}partition {_quote_tmdl(table.name)} = m", f"{TAB * (depth + 1)}mode: import"]
    lines.append(f"{TAB * (depth + 1)}source =")
    lines += _indented_block(m_query, depth + 3)
    return lines


def _measure_lines(measure: BIMeasure, table_name: str, depth: int = 1) -> list[str]:
    lines: list[str] = []
    if measure.description:
        for line in measure.description.split("\n"):
            lines.append(f"{TAB * depth}/// {line}" if line.strip() else f"{TAB * depth}///")
    if "\n" in measure.dax:
        lines.append(f"{TAB * depth}measure {_quote_tmdl(measure.name)} =")
        lines += _indented_block(measure.dax, depth + 2)
    else:
        lines.append(f"{TAB * depth}measure {_quote_tmdl(measure.name)} = {measure.dax}")
    lines.append(f"{TAB * (depth + 1)}formatString: {measure.format_string}")
    lines.append(f"{TAB * (depth + 1)}lineageTag: {_lineage_tag('measure', table_name, measure.name)}")
    lines.append("")
    return lines


def table_tmdl(
    table: BITable,
    measures: list[BIMeasure],
    delimiter: str,
    date_link_column: str | None,
    date_source_column: str | None,
) -> str:
    lines = [f"table {_quote_tmdl(table.name)}", f"{TAB}lineageTag: {_lineage_tag('table', table.name)}", ""]
    lines += _column_lines(table)

    if date_link_column and date_source_column:
        # The calendar joins on whole days, but a real transaction column is a timestamp, so a direct
        # relationship would match almost nothing. This calculated column is the date-only key.
        source = f"{_quote_tmdl(table.name)}[{date_source_column}]"
        lines.append(f"{TAB}/// Date-only key linking this table to the calendar, from {date_source_column}.")
        lines.append(f"{TAB}column {_quote_tmdl(date_link_column)} = DATE(YEAR({source}), MONTH({source}), DAY({source}))")
        lines.append(f"{TAB * 2}dataType: dateTime")
        lines.append(f"{TAB * 2}formatString: General Date")
        lines.append(f"{TAB * 2}lineageTag: {_lineage_tag('table', table.name, date_link_column)}")
        lines.append(f"{TAB * 2}summarizeBy: none")
        lines.append("")

    for measure in measures:
        lines += _measure_lines(measure, table.name)

    lines += _partition_lines(table, delimiter)
    lines.append("")
    return "\n".join(lines)


def calendar_tmdl(date_table: BIDateTable) -> str:
    """A calculated calendar over the dataset's own range, marked as the model's date table.

    `CALENDAR(DATE(...), DATE(...))` with literal dates, never `TODAY()` or `MIN(fact[date])`: the
    bounds are the ones `infer_date_bounds` measured, so the calendar cannot quietly extend into the
    sparse trailing tail `anchor_date` exists to trim.
    """
    start, end = date_table.start_date, date_table.end_date
    expression = (
        "ADDCOLUMNS(\n"
        f"    CALENDAR(DATE({start.year}, {start.month}, {start.day}), DATE({end.year}, {end.month}, {end.day})),\n"
        '    "Year", YEAR([Date]),\n'
        '    "Quarter", FORMAT([Date], "yyyy") & " Q" & FORMAT([Date], "Q"),\n'
        '    "Month", FORMAT([Date], "yyyy-MM"),\n'
        '    "MonthName", FORMAT([Date], "MMM yyyy")\n'
        ")"
    )
    name = date_table.table_name
    lines = [
        "/// Generated from this dataset's own date range, not from today's date.",
        f"table {_quote_tmdl(name)}",
        f"{TAB}lineageTag: {_lineage_tag('table', name)}",
        f"{TAB}dataCategory: Time",
        "",
    ]
    columns = [
        ("Date", "dateTime", "General Date", True),
        ("Year", "int64", "0", False),
        ("Quarter", "string", None, False),
        ("Month", "string", None, False),
        ("MonthName", "string", None, False),
    ]
    for column_name, data_type, format_string, is_key in columns:
        lines.append(f"{TAB}column {_quote_tmdl(column_name)}")
        lines.append(f"{TAB * 2}dataType: {data_type}")
        if is_key:
            lines.append(f"{TAB * 2}isKey")
        if format_string:
            lines.append(f"{TAB * 2}formatString: {format_string}")
        lines.append(f"{TAB * 2}lineageTag: {_lineage_tag('table', name, column_name)}")
        lines.append(f"{TAB * 2}summarizeBy: none")
        lines.append(f"{TAB * 2}sourceColumn: [{column_name}]")
        lines.append(f"{TAB * 2}type: calculatedTableColumn")
        lines.append("")

    lines.append(f"{TAB}partition {_quote_tmdl(name)} = calculated")
    lines.append(f"{TAB * 2}mode: import")
    lines.append(f"{TAB * 2}source =")
    lines += _indented_block(expression, 4)
    lines.append("")
    return "\n".join(lines)


def relationships_tmdl(relationships: list[BIRelationship]) -> str:
    lines: list[str] = []
    for relationship in relationships:
        name = _lineage_tag(
            "rel",
            relationship.from_table,
            relationship.from_column,
            relationship.to_table,
            relationship.to_column,
        )
        # No `///` description here, unlike tables, columns and measures: a TMSL Relationship has no
        # `description` property, and Desktop rejects the whole project with "Property 'description'
        # is unknown and is not expected in the situation it appears" -- found on the first real open
        # in Power BI Desktop 2.157. The reason an edge is inactive is carried in the export README
        # instead, which is where a reader looks for it anyway.
        lines.append(f"relationship {_quote_tmdl(name)}")
        if not relationship.is_active:
            lines.append(f"{TAB}isActive: false")
        if relationship.cardinality == "oneToOne":
            lines.append(f"{TAB}fromCardinality: one")
            lines.append(f"{TAB}toCardinality: one")
        elif relationship.cardinality == "manyToMany":
            lines.append(f"{TAB}fromCardinality: many")
            lines.append(f"{TAB}toCardinality: many")
        lines.append(
            f"{TAB}fromColumn: {_quote_tmdl(relationship.from_table)}.{_quote_tmdl(relationship.from_column)}"
        )
        lines.append(
            f"{TAB}toColumn: {_quote_tmdl(relationship.to_table)}.{_quote_tmdl(relationship.to_column)}"
        )
        lines.append("")
    return "\n".join(lines)


def model_tmdl(model: BIModel) -> str:
    lines = [
        "model Model",
        f"{TAB}culture: en-US",
        f"{TAB}defaultPowerBIDataSourceVersion: powerBI_V3",
        f"{TAB}sourceQueryCulture: en-US",
        "",
    ]
    names = [table.name for table in model.tables]
    if model.date_table:
        names.append(model.date_table.table_name)
    for name in names:
        lines.append(f"ref table {_quote_tmdl(name)}")
    lines.append("")
    return "\n".join(lines)


def expressions_tmdl(default_folder: str) -> str:
    """The `DataFolder` parameter every partition reads from."""
    return (
        "/// Folder holding this export's CSV files. Set it to wherever you unzipped the `data`\n"
        "/// folder, with a trailing backslash.\n"
        f'expression DataFolder = "{_escape_m(default_folder)}" '
        'meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n'
        f"{TAB}lineageTag: {_lineage_tag('expression', 'DataFolder')}\n"
        f"{TAB}annotation PBI_NavigationStepName = Navigation\n"
        f"{TAB}annotation PBI_ResultType = Text\n"
    )


def database_tmdl() -> str:
    return "database\n\tcompatibilityLevel: 1550\n"


def platform_json(display_name: str, artifact_type: str) -> str:
    return json.dumps(
        {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {"type": artifact_type, "displayName": display_name},
            "config": {"version": "2.0", "logicalId": _lineage_tag("artifact", artifact_type, display_name)},
        },
        indent=2,
    )


def pbip_json(name: str) -> str:
    return json.dumps(
        {
            "version": "1.0",
            "artifacts": [{"report": {"path": f"{name}.Report"}}],
            "settings": {"enableAutoRecovery": True},
        },
        indent=2,
    )


def pbism_json() -> str:
    return json.dumps({"version": "4.0", "settings": {}}, indent=2)


def pbir_json(name: str) -> str:
    return json.dumps(
        {"version": "1.0", "datasetReference": {"byPath": {"path": f"../{name}.SemanticModel"}}},
        indent=2,
    )


def report_json() -> str:
    """One empty page. The model is Phase 9's deliverable; visuals are the reader's to build.

    Kept minimal on purpose -- the report layout format is the least stable part of PBIP, and an
    empty page is the smallest thing that still makes the project openable.
    """
    return json.dumps(
        {
            "config": json.dumps({"version": "5.43", "activeSectionIndex": 0}),
            "layoutOptimization": 0,
            "resourcePackages": [],
            "sections": [
                {
                    "name": "ReportSection",
                    "displayName": "Page 1",
                    "filters": "[]",
                    "ordinal": 0,
                    "visualContainers": [],
                    "config": "{}",
                    "displayOption": 1,
                    "width": 1280,
                    "height": 720,
                }
            ],
            "filters": "[]",
        },
        indent=2,
    )


def date_link_column_name() -> str:
    return "InsightFlowDate"


def calendar_relationship(model: BIModel) -> BIRelationship | None:
    if model.date_table is None:
        return None
    return BIRelationship(
        from_table=model.date_table.related_table,
        from_column=date_link_column_name(),
        to_table=model.date_table.table_name,
        to_column="Date",
        cardinality="manyToOne",
    )


def _iso(value: date) -> str:
    return value.isoformat()
