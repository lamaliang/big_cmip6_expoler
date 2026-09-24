#!/usr/bin/env python3
"""
Parse a CMIP6 archive file listing (DRS-style paths) and build a set of
aggregated JSON files for the web explorer:

  data/models.json              - lightweight list of all models + counts
  data/model_<MODEL>.json        - per-model detail (experiments -> variables -> files)
  data/variables_index.json      - global variable -> {models: {model: [experiments]}}

Usage:
  python3 build_cmip6.py <filelist.txt> <output_dir> <cmor_lookup.json>

filelist.txt: one absolute path per line, e.g.
  /lfs/archive/CMIP6/ACCESS-CM2/historical/atmos/mon/r1i1p1f1/tas_Amon_..._gn_..-...nc

Some experiments (DCPP decadal-prediction hindcasts, e.g. dcppA-hindcast) have
an extra "start date" folder between the experiment and the realm, e.g.:
  .../dcppA-hindcast/s1961/atmos/day/r1i1p1f1/...nc
These are NOT flattened into dozens of pseudo-experiments ("dcppA-hindcast
(s1961)", "dcppA-hindcast (s1962)", ...) - they're kept nested under the base
experiment as `sub`, so the UI can show one "dcppA-hindcast" entry that
expands into its start-date members.

Designed to be re-run from cron: it fully regenerates the output dir contents
each run (idempotent), so it's safe to call repeatedly on a fresh `find` scan.
"""
import sys, os, re, json
from collections import defaultdict

REALMS = {'land', 'seaIce', 'ocean', 'ocnBgchem', 'landIce', 'aerosol', 'atmos'}
ARCHIVE_ROOT_MARKER = '/CMIP6/'

def fmt_time(t):
    if t is None:
        return None
    if '-' not in t:
        return t
    s, e = t.split('-', 1)
    def fmt(x):
        if len(x) == 6:
            return f"{x[:4]}-{x[4:6]}"
        elif len(x) == 8:
            return f"{x[:4]}-{x[4:6]}-{x[6:8]}"
        elif len(x) == 12:
            return f"{x[:4]}-{x[4:6]}-{x[6:8]} {x[8:10]}:{x[10:12]}"
        return x
    return f"{fmt(s)} ~ {fmt(e)}"

TIME_RE = re.compile(r'^\d{4,12}-\d{4,12}$')

def parse_line(line):
    """Return dict or None if the line doesn't fit the standard CMIP6 DRS shape."""
    if not line.endswith('.nc'):
        return None
    idx = line.find(ARCHIVE_ROOT_MARKER)
    if idx == -1:
        return None
    rest = line[idx + len(ARCHIVE_ROOT_MARKER):]
    segs = rest.split('/')
    ridx = None
    for i, s in enumerate(segs):
        if s in REALMS:
            ridx = i
            break
    if ridx is None or len(segs) < ridx + 4:
        return None  # non-standard tree (e.g. derived storm-tracker products) - skip

    model = segs[0]
    exp_path_segs = segs[1:ridx]
    if not exp_path_segs:
        return None
    experiment = exp_path_segs[0]
    subexperiment = exp_path_segs[1] if len(exp_path_segs) > 1 else None  # e.g. "s1961"
    realm = segs[ridx]
    freq_folder = segs[ridx + 1]
    member = segs[ridx + 2]
    fname = segs[ridx + 3]
    if fname != segs[-1]:
        return None  # deeper than expected, non-standard
    parts = fname[:-3].split('_')  # strip .nc
    if len(parts) < 6:
        return None
    varname = parts[0]
    table = parts[1]
    last = parts[-1]
    grid = parts[-2]
    time = last if TIME_RE.match(last) else None
    if time is None:
        grid = last  # fx-like: no time token, last part is grid

    return {
        'model': model, 'experiment': experiment, 'subexperiment': subexperiment,
        'realm': realm, 'freq_folder': freq_folder, 'member': member,
        'varname': varname, 'table': table, 'grid': grid,
        'time': fmt_time(time), 'path': line,
    }


def new_var_node():
    return {'table': None, 'realm': None, 'files': [], '_seen': set()}


def main():
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <filelist.txt> <output_dir> <cmor_lookup.json>", file=sys.stderr)
        sys.exit(1)

    filelist_path, out_dir, cmor_lookup_path = sys.argv[1:4]
    cmor_lookup = json.load(open(cmor_lookup_path))  # "table::var" -> {long_name, units}

    def get_meta(table, var):
        key = f"{table}::{var}"
        if key in cmor_lookup:
            return cmor_lookup[key]
        for k, v in cmor_lookup.items():
            if k.endswith(f"::{var}"):
                return v
        return {'long_name': '', 'units': ''}

    # model -> experiment -> {'direct': {var: node}, 'sub': {subexp: {var: node}}}
    tree = defaultdict(lambda: defaultdict(lambda: {'direct': defaultdict(new_var_node),
                                                      'sub': defaultdict(lambda: defaultdict(new_var_node))}))
    # var -> {model: set(base experiment names)}  -- sub-experiments collapse into their base name here
    var_index = defaultdict(lambda: defaultdict(set))

    n_total = 0
    n_ok = 0
    n_skipped = 0

    with open(filelist_path, errors='replace') as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            n_total += 1
            rec = parse_line(line)
            if rec is None:
                n_skipped += 1
                continue
            n_ok += 1
            exp_node = tree[rec['model']][rec['experiment']]
            if rec['subexperiment'] is None:
                node = exp_node['direct'][rec['varname']]
            else:
                node = exp_node['sub'][rec['subexperiment']][rec['varname']]
            node['table'] = rec['table']
            node['realm'] = rec['realm']
            key = (rec['member'], rec['time'], rec['path'])
            if key in node['_seen']:
                continue
            node['_seen'].add(key)
            node['files'].append({'member': rec['member'], 'time': rec['time'], 'path': rec['path']})
            var_index[rec['varname']][rec['model']].add(rec['experiment'])

    os.makedirs(out_dir, exist_ok=True)

    def build_var_out(v, node):
        meta = get_meta(node['table'], v)
        files = sorted(node['files'], key=lambda x: (x['time'] or '', x['member'], x['path']))
        return {
            'table': node['table'], 'realm': node['realm'],
            'long_name': meta['long_name'], 'units': meta['units'],
            'files': files,
        }, len(files)

    # ---- per-model files ----
    models_summary = []
    for model, experiments in tree.items():
        n_exp = len(experiments)
        n_files = 0
        n_vars = set()
        exp_out = {}
        for exp, node in experiments.items():
            direct_out = {}
            for v, vnode in node['direct'].items():
                var_out, nf = build_var_out(v, vnode)
                direct_out[v] = var_out
                n_files += nf
                n_vars.add(v)

            sub_out = {}
            for subexp, vars_ in sorted(node['sub'].items()):
                sub_var_out = {}
                for v, vnode in vars_.items():
                    var_out, nf = build_var_out(v, vnode)
                    sub_var_out[v] = var_out
                    n_files += nf
                    n_vars.add(v)
                sub_out[subexp] = sub_var_out

            exp_out[exp] = {'direct': direct_out, 'sub': sub_out}

        safe_name = re.sub(r'[^A-Za-z0-9_.-]', '_', model)
        fname = f"model_{safe_name}.json"
        json.dump({'model': model, 'experiments': exp_out},
                   open(os.path.join(out_dir, fname), 'w'), ensure_ascii=False, separators=(',', ':'))
        models_summary.append({
            'model': model, 'file': fname,
            'n_experiments': n_exp, 'n_variables': len(n_vars), 'n_files': n_files,
        })

    models_summary.sort(key=lambda m: m['model'])
    json.dump({'models': models_summary, 'n_total_files_seen': n_total, 'n_parsed': n_ok, 'n_skipped': n_skipped},
               open(os.path.join(out_dir, 'models.json'), 'w'), ensure_ascii=False, indent=0)

    # ---- global variable index (no file paths - just model/experiment presence) ----
    var_index_out = {}
    for v, models in var_index.items():
        meta = {'long_name': '', 'units': ''}
        for model, experiments in tree.items():
            for exp, node in experiments.items():
                if v in node['direct']:
                    meta = get_meta(node['direct'][v]['table'], v)
                    break
                for subexp, vars_ in node['sub'].items():
                    if v in vars_:
                        meta = get_meta(vars_[v]['table'], v)
                        break
                if meta['long_name']:
                    break
            if meta['long_name']:
                break
        var_index_out[v] = {
            'long_name': meta['long_name'],
            'units': meta['units'],
            'models': {m: sorted(exps) for m, exps in models.items()},
        }
    json.dump(var_index_out, open(os.path.join(out_dir, 'variables_index.json'), 'w'),
               ensure_ascii=False, separators=(',', ':'))

    print(f"parsed {n_ok}/{n_total} lines ({n_skipped} skipped/non-standard)")
    print(f"{len(tree)} models, {len(var_index_out)} unique variables")
    print(f"wrote {len(models_summary)} per-model files + models.json + variables_index.json to {out_dir}/")


if __name__ == '__main__':
    main()
