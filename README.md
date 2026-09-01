# visorADN

A web editor for circular DNA sequences (plasmids, 2–20 kb): import GenBank or
FASTA, view the circular and linear maps side by side, edit the sequence, and
undo any of it. Portfolio project, not a clinical tool.

![The editor showing pUC19 with three cut sites selected](docs/screenshot.png)

- **Backend** — Python 3.11, FastAPI, Pydantic v2, Biopython, SQLAlchemy 2 + SQLite
- **Frontend** — Next.js 15 (App Router), TypeScript, Tailwind 4, [`seqviz`](https://github.com/Lattice-Automation/seqviz)

---

## The idea: current state is derived, never stored

This is the part worth understanding first, because everything else follows
from it.

A construct persists exactly three things:

```
base_sequence     the sequence as it was imported, never mutated
base_features     the features as they were imported, never mutated
operations[]      an append-only, ordered log of edits
```

The sequence you see on screen is not in the database. It is computed on every
read by folding the log over the base:

```python
# backend/app/domain/replay.py
def replay(base_seq, base_features, ops, *, is_circular=True) -> ConstructState
```

`replay()` is a pure function — no I/O, no FastAPI, no SQLAlchemy — so the
interesting logic is testable in isolation, and it is where every coordinate
rule lives.

**Why bother?** Three things fall out for free:

1. **Undo is a boolean.** `POST /undo` flips `reverted = true` on the last live
   operation. The next read simply skips it. Nothing is recomputed by hand and
   nothing can drift, because there is no stored state to drift from. Redo
   flips it back.
2. **The history is auditable.** `GET /history` is the literal list of edits,
   including the undone ones. Nothing is lost to an in-place mutation.
3. **The rebasing rules have exactly one home.** Every coordinate shift lives in
   `replay.py` and is exercised by unit tests that never touch a database.

The one thing to be careful about: applying a new operation while some are
undone **deletes** the undone ones. That is text-editor semantics, not git —
there is no branching history.

```
ops:  [0 insert] [1 delete] [2 revcomp]
undo x2                     ↓ reverted    ↓ reverted
apply "insert"              ✗ deleted     ✗ deleted   [1 insert]  ← new
```

### Operations

| kind             | payload                    | notes                                     |
|------------------|----------------------------|-------------------------------------------|
| `insert`         | `{pos, seq}`               |                                           |
| `delete`         | `{start, end}`             |                                           |
| `replace`        | `{start, end, seq}`        | implemented as delete + insert            |
| `revcomp_region` | `{start, end}`             |                                           |
| `add_feature`    | `{feature}`                |                                           |
| `remove_feature` | `{feature_id}`             |                                           |
| `update_feature` | `{feature_id, patch}`      |                                           |
| `set_origin`     | `{pos}`                    | rotates a circular construct              |

---

## Coordinates

0-based, `start` inclusive, `end` exclusive — Python slice semantics.

On a **circular** construct an interval may cross the origin, in which case
`start > end` (e.g. `start=4900, end=120` on a 5000 bp plasmid). `start == end`
means the whole molecule. Zero-length features are rejected.

### How rebasing works

Every length-changing edit moves the features after it. The rules, all covered
one-by-one in `tests/test_rebasing.py`:

**insert at `pos`** — one endpoint rule handles every case, wraparound included:

- `start` moves when `start >= pos` (the insert lands before the feature)
- `end` moves when `end > pos` (the insert lands before the feature's last base)

A feature that strictly contains `pos` therefore keeps its start and grows its
end: it swallows the inserted bases.

**delete `[start, end)`**

- feature entirely inside the range → dropped, with a warning
- feature entirely downstream → both endpoints move back
- feature partially overlapped → clipped to the border and flagged `truncated`

**revcomp_region `[start, end)`** — contained features flip strand and mirror
within the range (`new_start = start + (end - old_end)`). A feature that only
partially overlaps the range is left untouched and reported as a warning: there
is no honest single interval for it afterwards.

**set_origin(pos)** — every coordinate moves by `-pos` modulo the length.

### Wraparound is handled by decomposition, not special cases

A possibly-wrapping interval is split into its one or two **linear** segments
(`circular.py: segments()`), the plain linear rule runs on each, and the pieces
are rejoined (`recombine()`). That keeps one implementation of each rule
instead of a linear and a circular variant.

Operations whose *range* wraps are handled by rotating the construct so the
range starts at 0, applying the linear operation, and rotating back — so
`revcomp_region(4900, 120)` leaves every base outside the range at exactly the
coordinate it had before.

Pattern and enzyme searches concatenate `seq + seq[:k-1]` and drop hits that
start past the end, so a site spanning the origin is found exactly once.

---

## Running it

Two processes. The backend serves on `:8000`, the frontend on `:3000`.

### Backend

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Interactive API docs: <http://localhost:8000/docs>.
The SQLite file lands at `backend/visoradn.db`; override with `DATABASE_URL`.

### Frontend

```bash
cd frontend
pnpm install
pnpm dev
```

Then open <http://localhost:3000>, import
`backend/tests/data/puc19_annotated.gb`, and you should see pUC19 at 2686 bp
with its 18 features.

Point the UI at a different API with `NEXT_PUBLIC_API_BASE_URL`; allow extra
browser origins with `CORS_ORIGINS` on the backend.

### Tests

```bash
cd backend && uv run pytest
```

173 tests: one per rebasing rule, explicit wraparound cases, GenBank round
trips against two real pUC19 records, and the HTTP surface end to end.

---

## API

| Method | Path                                | |
|--------|-------------------------------------|-|
| POST   | `/api/constructs`                   | create empty or from a raw sequence |
| POST   | `/api/constructs/import`            | multipart `.fasta` / `.gb` / `.gbk` |
| GET    | `/api/constructs`                   | listing |
| GET    | `/api/constructs/{id}`              | metadata + current derived state |
| DELETE | `/api/constructs/{id}`              | |
| POST   | `/api/constructs/{id}/operations`   | apply one operation |
| POST   | `/api/constructs/{id}/undo`         | |
| POST   | `/api/constructs/{id}/redo`         | |
| GET    | `/api/constructs/{id}/history`      | operations with their `reverted` flag |
| GET    | `/api/constructs/{id}/export`       | `?format=genbank\|fasta` |
| GET    | `/api/constructs/{id}/enzymes`      | `?all=true`, `?names=EcoRI,BamHI` |
| GET    | `/api/constructs/{id}/orfs`         | `?min_length=300` |

`GET /{id}` returns `sequence`, `features`, `length`, `is_circular`,
`gc_content`, `warnings`, `can_undo` and `can_redo`.

Warnings are part of the derived state: they are recomputed on every replay,
so undoing a truncating delete makes its warning disappear along with the
truncation.

`/enzymes` returns single cutters from a curated set of ~57 cloning workhorses
by default; `?all=true` widens to every commercially available enzyme and
includes multi-cutters. `/orfs` scans all six frames with the standard genetic
code (NCBI table 1) and, on a circular construct, finds ORFs that run through
the origin.

---

## Import / export

Import reads the first record of a FASTA or GenBank file (extra records are
reported as a warning), takes circularity from the LOCUS `topology` field, and
names features from `label`, `gene`, `product`, `standard_name` or `note`, in
that order, falling back to the feature type. `join(a..len,1..b)` locations are
rejoined into origin-crossing features. Sequences are validated against
`ACGTN` + the IUPAC ambiguity codes `RYSWKMBDHV`; anything else is a 422.

Import is deliberately forgiving about the rest: the checked-in pUC19 fixture
contains a genuinely malformed location line, and the importer skips just that
feature and says so rather than failing the file.

Export writes the **current** derived state, not the base, and round-trips
sequence, features, strands, colours (via the ApE/SnapGene `ApEinfo_fwdcolor`
qualifier), origin-crossing features and truncation flags. Re-importing an
exported file gives back an equivalent construct — there is a test for it.

---

## Layout

```
backend/
  app/
    main.py
    api/routes/constructs.py   HTTP surface
    api/schemas.py             request/response bodies
    domain/                    ← pure, no I/O
      models.py                Feature, Operation, ConstructState
      replay.py                replay() and every rebasing rule
      circular.py              wraparound helpers
      seqio.py                 Biopython import/export
      analysis.py              enzymes, ORFs, GC
    db/                        SQLAlchemy models + session
  tests/
    test_rebasing.py  test_circular.py  test_replay.py
    test_seqio.py     test_analysis.py  test_api.py
    data/                      two real pUC19 GenBank records
frontend/
  app/constructs/[id]/page.tsx the editor
  app/page.tsx                 listing + import
  components/                  Toolbar, FeatureList, HistoryPanel,
                               EnzymePanel, SeqVizPane, Toasts
  lib/                         api.ts, types.ts, sequence.ts, useConstruct.ts
```

---

## Notes and deviations from the brief

- **`seqviz` prop name.** The brief said `viewType="both"`; the actual prop in
  seqviz 3.x is `viewer`. Used the real one.
- **`Bio.Restriction.CommonEnzymes` does not exist** in Biopython 1.88. The
  real sets are `AllEnzymes` (1088) and `CommOnly` (623), neither of which
  makes a usable checkbox list, so `/enzymes` defaults to a curated set of
  cloning workhorses defined in `analysis.py` and `?all=true` widens to
  `CommOnly`.
- **`Feature.truncated`** was added to the model so the flag the brief asks for
  can travel through the API and be shown in the UI.
- **`replay()` takes `is_circular`** as a keyword argument. The signature in
  the brief has no way to know the topology, and every rebasing rule needs it.
- **`create-next-app` installs Next 16**; pinned back to 15 as specified.
- **pUC19 fixtures came from GitHub, not NCBI.** `eutils.ncbi.nlm.nih.gov` is
  blocked by this environment's egress policy, so accession L09136 could not be
  fetched directly. `backend/tests/data/` holds two equivalent 2686 bp pUC19
  records mirrored from public repositories; see the README there.
- **Optimistic UI is sequence-only.** Edits show the predicted sequence
  immediately and roll back if the POST fails, but feature positions come from
  the server. Re-implementing the rebasing rules in TypeScript to predict them
  client-side would fork the one thing this project is trying to get right.
- No authentication, no multi-user — as specified.
