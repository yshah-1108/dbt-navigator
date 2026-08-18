# Fixtures: formats, typing, and mocking

Most unit test failures that are not real defects come from the fixture rather than the model. A fixture is data you wrote by hand, in a format that is loosely typed, injected into SQL that expects specific types. This document covers what actually happens to a fixture row on its way into the query, and how to mock the inputs a model does not read from a plain table.

## What dbt does with a fixture

Every `given` input is compiled into a literal `select` — a `union all` of your rows — and substituted for the `ref()` or `source()` it replaces. Two consequences follow, and they explain nearly every confusing failure:

1. **Types come from the literal, not from the real upstream table.** If the model casts or compares a column, the type of your fixture value is what the warehouse sees. A quoted `'1'` and a bare `1` are different types, and on a strict engine the difference is an error rather than a coercion.
2. **Columns you did not supply do not exist** in the substituted relation — unless the model selects them, in which case dbt fills them with null so the query compiles. That is the mechanism behind "only supply the columns the logic touches", and it is also why a fixture can pass while silently exercising a null path you did not intend.

## Choosing a format

| Format | Where | Use when | Watch for |
|---|---|---|---|
| `dict` | Inline `rows:` | Default. A handful of rows, a handful of columns | Verbose past about six columns |
| `csv` | Inline string, or `fixture: <name>` file | Many rows, or naturally tabular data | Everything is a string until cast; empty field is ambiguous |
| `sql` | Inline SQL string | **Required for an ephemeral input.** Also for generating rows, or forcing an exact type | You now maintain SQL, and it must be valid for the target dialect |

`format` can be set once at the unit-test level and overridden per input. A file fixture lives in `fixtures/<name>.csv` (or `.sql`) under a configured test path, and `fixture:` names it without the extension.

**Prefer a file fixture once a fixture exceeds roughly ten rows, or is shared between tests.** Inline rows that scroll past a screen make the assertion unreadable, which is the failure mode that ends with someone deleting the test rather than reading it.

## Nulls, empty strings, and the CSV trap

This is the single most common fixture defect.

| Format | Null | Empty string |
|---|---|---|
| `dict` | `null` (unquoted) | `''` |
| `csv` | An empty field | **Not expressible** |

In a CSV fixture, `a,,c` gives a null in the middle column. There is no way to write an empty string. If the model distinguishes `''` from `null` — and any model doing `nullif(<col>, '')` or `coalesce()` on a string does — a CSV fixture **cannot** express the case you need to test. Use `dict`, or `sql` with an explicit `''`.

The reverse trap: in `dict` format, `column: ` with nothing after it is null, and `column: ''` is an empty string, and the two look almost identical in review.

For a column the model reads but whose value is irrelevant to the assertion, leaving it out is better than supplying a plausible value — a supplied value invites a reader to think it matters.

## Forcing a type

When the model's logic depends on a type, do not rely on inference:

```yaml
given:
  - input: ref('<upstream_model>')
    format: sql
    rows: |
      select
          cast('<id_value>' as varchar)       as <id_column>,
          cast('2024-01-01' as date)          as <date_column>,
          cast(null as decimal(38,6))         as <measure_column>
```

Cases where this is worth the extra verbosity:

- **A decimal measure.** A bare `100` may arrive as an integer, and integer division then produces `0` where the model would produce `0.5` on real data. The test passes or fails for a reason unrelated to the logic.
- **A timestamp with a timezone.** The distinction between a zoned and an unzoned timestamp is not expressible in `dict` or `csv`, and it is exactly the distinction a date-boundary test is about.
- **A null in a column whose type the model casts.** `cast(null as varchar)` and an untyped null are different on some engines.
- **A string that looks like a number.** `dict` will hand `1` to the query as a number; if the real column is a string identifier, any string function in the model behaves differently.

`format: sql` is also the only way to generate many rows procedurally, and the only supported format for an ephemeral input — an ephemeral model has no relation to substitute, so dbt needs a subquery.

## Mocking a source

Identical to mocking a model, with `source()` in place of `ref()`:

```yaml
given:
  - input: source('<source_name>', '<table_name>')
    rows:
      - {<column>: <value>}
```

The name and table must match the `source()` call in the model exactly. Two failures follow from this and both read as broken models:

- **A `source()` left unmocked** produces a "node not found" style error at compile time. Every `ref()` *and* `source()` in the model needs an entry.
- **A `source()` whose table is quoted or cased differently** in the YAML than in the model does not match, and dbt reports the missing input rather than the mismatch.

Mocking a source is the only way to test source-facing logic — casting, renaming, `nullif`, deduplication — without landing rows in the warehouse. On a project where source-facing models are pure column mapping this is not worth doing; where they do the project's cleaning, it is where the highest-value unit tests live.

## Overrides: macros, vars, and env vars

```yaml
    overrides:
      macros:
        is_incremental: false
        <other_macro_name>: <return_value>
      vars:
        <var_name>: <value>
      env_vars:
        <ENV_VAR_NAME>: <value>
```

`is_incremental` is the important one — see the incremental section in the parent skill. The general form matters for two other cases:

- **A macro returning a date or timestamp** used as a boundary. Overriding it makes the test deterministic; without the override, a test asserting behaviour relative to "now" passes today and fails in a month. **A unit test whose result depends on the current time is a scheduled failure.**
- **A var that switches logic** between environments. Both branches are testable, and only one is ever exercised by a real run.

Only macros the model calls can be overridden, and the override supplies a return value, not an implementation. A macro whose behaviour depends on its arguments cannot be usefully overridden — restructure the model or accept that the macro is out of scope.

## Fixtures for a model with a `this` reference

An incremental model referencing `this` needs `input: this` supplied like any other relation. The rows represent **what the table already contains**, and the expectation is what the `select` produces for merging — never the post-merge table. The parent skill covers the boundary-predicate test this enables.

A model referencing `this` outside an `is_incremental()` block — for example to read its own maximum on every run — needs `input: this` in every unit test, including the full-refresh one, where it is usually correct to supply zero rows:

```yaml
given:
  - input: this
    rows: []
```

## Keeping fixtures from rotting

Fixtures are the part of a unit test that decays, because they encode a source's shape at the moment you wrote them.

- **Supply the minimum.** Every extra column is a future edit. An over-specified fixture breaks when an unrelated column is added upstream, and the resulting failure teaches the reader that unit tests are noise.
- **One concern per test.** A test asserting six behaviours over twenty fixture rows tells you something broke. A test asserting one tells you what.
- **When a source's schema changes, the unit tests are part of the change.** A fixture asserting a shape the source no longer produces tests a world that no longer exists — and it will keep passing.
- **Name the fixture file after the scenario**, not the model, so a reader can tell from the filename whether it is still relevant.

## Checklist

- [ ] Format chosen deliberately; `sql` used for ephemeral inputs and where a type must be forced
- [ ] Empty-string cases not written in `csv`, where they are inexpressible
- [ ] Decimal measures and zoned timestamps explicitly cast where the logic depends on the type
- [ ] Every `ref()` and `source()` in the model present as an input, with names matching exactly
- [ ] `input: this` supplied wherever the model references itself, including full-refresh tests
- [ ] Any time-dependent macro overridden so the result is deterministic
- [ ] Only the columns the logic touches supplied
- [ ] Fixtures over ~10 rows moved to a file, named after the scenario
- [ ] Test observed to fail when the logic is deliberately broken

## Failure modes

1. **An empty-string case written as a CSV fixture.** The field is a null, the model's `nullif` branch is never exercised, and the test certifies behaviour it did not test.
2. **An untyped integer standing in for a decimal.** Integer division truncates in the test and not in production, or the reverse. The failure looks like a logic bug.
3. **A fixture value that is a number where the real column is a string.** Every string function in the model behaves differently, and the test proves nothing about production.
4. **A `source()` mocked with a differently-cased table name.** dbt reports a missing input; the actual cause is a typo in the YAML.
5. **A test that depends on the current date.** Passes for a while, then fails on a boundary nobody changed.
6. **An over-specified fixture.** Breaks on an unrelated upstream column addition, and gets deleted rather than fixed.
7. **`input: this` omitted from a full-refresh test** on a model that reads itself outside the incremental branch. Compilation fails and the message points at the model.
8. **A fixture kept after the source changed shape.** It passes, and it asserts the old world.
