#!/usr/bin/env python3
''' Regenerate the two generated ConfigMaps in this directory from the working tree:

      airflow-dags.yaml     <- data/airflow/dags/{io,ai}
      airflow-modules.yaml  <- docker/airflow/src

    Run after changing a DAG or any of the Sonador integration modules:

      python3 k8s/airflow-ai/gen-configmaps.py
      kubectl apply -f k8s/airflow-ai/airflow-dags.yaml \\
                    -f k8s/airflow-ai/airflow-modules.yaml

    DAG ConfigMaps are mounted as directories, so edits propagate to running pods without a
    restart (the dag-processor picks them up on its next scan). The module ConfigMap is
    mounted with subPath, which does NOT propagate -- restart the components after changing
    anything under docker/airflow/src.
'''
import os, sys

# --check renders the ConfigMaps and compares them with what is on disk instead of writing,
# exiting non-zero if either is stale. The generated files are committed so the manifest set
# reads as a complete blueprint, which means they can drift from their sources; this is what
# makes that drift detectable from a pre-commit hook or CI job.
CHECK_ONLY = '--check' in sys.argv[1:]

HERE = os.path.dirname(os.path.abspath(__file__))          # <repo>/k8s/airflow-ai
ROOT = os.path.dirname(os.path.dirname(HERE))              # <repo>

DAG_SRC = os.path.join(ROOT, 'data', 'airflow', 'dags')
MOD_SRC = os.path.join(ROOT, 'docker', 'airflow', 'src')

# Kubernetes caps a ConfigMap at 1 MiB of data.
CONFIGMAP_LIMIT = 1024 * 1024

BANNER = '''# GENERATED FILE -- do not edit by hand. Regenerate with:
#
#   python3 k8s/airflow-ai/gen-configmaps.py
#
'''

# (configmap name, source subdirectory, mount description)
DAG_MAPS = [
    ('airflow-dags-io', 'io', '/opt/airflow/dags/sonador'),
    ('airflow-dags-ai', 'ai', '/opt/airflow/dags/ai'),
]

# Shipped as a key in every DAG ConfigMap. Kubernetes surfaces a ConfigMap volume as a
# symlink farm -- "..data" is a symlink to a timestamped directory, and both are directories:
#
#     ..2026_08_14_19_06_49.938574535/   hello-world.py
#     ..data -> ..2026_08_14_19_06_49.938574535
#     hello-world.py -> ..data/hello-world.py
#
# Airflow walks the DAG folder with followlinks=True, sees the same real directory twice, and
# aborts the whole DagProcessorJob with "Detected recursive loop when walking DAG directory".
# In airflow/utils/file.py the ignore patterns are applied to `dirs` immediately BEFORE the
# recursion check, so pruning the dot-dot entries here prevents the error rather than merely
# hiding it. Verified against this image: without it the walk raises; with it all DAGs load.
#
# The pattern is glob syntax -- [core] dag_ignore_file_syntax defaults to "glob" in Airflow 3,
# not the regexp of earlier versions.
AIRFLOWIGNORE = '''# Kubernetes ConfigMap volumes present ..data and ..<timestamp>/ as directories pointing at
# the same content. Airflow follows symlinks when scanning for DAGs and treats that as a
# recursive loop. Prune them before the walk descends.
..*
'''

# (configmap key, source filename) -- the key is the filename the pod sees
MODULES = [
    ('sonador_sso.py', 'sonador-sso.webserver_config.py'),
    ('sonador_auth.py', 'airflow-api.sonador_auth.py'),
    ('sonador_hook.py', 'airflow-api.sonador_hook.py'),
    ('object_storage_hook.py', 'airflow-api.object_storage_hook.py'),
    ('airflow-totalsegmentator.execute.py', 'airflow-totalsegmentator.execute.py'),
]


def block(body, indent='    '):
    ''' Indent a file body for a YAML literal block, collapsing whitespace-only lines. '''
    return ''.join(('%s%s\n' % (indent, ln)) if ln.strip() else '\n'
                   for ln in body.splitlines())


def emit(path, docs):
    ''' Render the ConfigMap and either write it or, under --check, compare it with what is
        on disk. Returns True if the file on disk is already current.
    '''
    total = sum(len(b) for _, keys in docs for _, b in keys)
    if total > CONFIGMAP_LIMIT:
        sys.exit('ERROR: %s would be %d bytes, over the 1 MiB ConfigMap limit. '
                 'Switch to git-sync or a shared volume for DAG delivery.' % (path, total))
    out = [BANNER]
    for header, keys in docs:
        out.append(header)
        for key, body in keys:
            out.append('  %s: |\n' % key)
            out.append(block(body))
            out.append('\n')
    rendered = ''.join(out)
    rel = os.path.relpath(path, ROOT)
    current = open(path).read() if os.path.exists(path) else None

    if CHECK_ONLY:
        if current == rendered:
            print('ok       %-44s (%d keys, %d bytes)'
                  % (rel, sum(len(k) for _, k in docs), total))
            return True
        print('STALE    %s -- regenerate with: python3 k8s/airflow-ai/gen-configmaps.py' % rel)
        return False

    if current == rendered:
        print('current  %-44s (%d keys, %d bytes)'
              % (rel, sum(len(k) for _, k in docs), total))
        return True
    with open(path, 'w') as fh:
        fh.write(rendered)
    print('wrote    %-44s (%d keys, %d bytes)'
          % (rel, sum(len(k) for _, k in docs), total))
    return True


def main():
    fresh = []

    # --- DAGs ---------------------------------------------------------------------------
    docs = []
    for name, subdir, mount in DAG_MAPS:
        srcdir = os.path.join(DAG_SRC, subdir)
        keys = [('.airflowignore', AIRFLOWIGNORE)]
        for fname in sorted(os.listdir(srcdir)):
            if not fname.endswith('.py'):
                continue
            with open(os.path.join(srcdir, fname)) as fh:
                keys.append((fname, fh.read()))
        docs.append((
            '---\n'
            '# data/airflow/dags/%s -> mounted as a directory at %s\n'
            'kind: ConfigMap\n'
            'apiVersion: v1\n'
            'metadata:\n'
            '  name: %s\n'
            '  namespace: airflow-ai\n'
            '  labels:\n'
            '    app.kubernetes.io/name: airflow\n'
            'data:\n' % (subdir, mount, name),
            keys))
    fresh.append(emit(os.path.join(HERE, 'airflow-dags.yaml'), docs))

    # --- Sonador integration modules --------------------------------------------------
    keys = []
    for key, fname in MODULES:
        with open(os.path.join(MOD_SRC, fname)) as fh:
            keys.append((key, fh.read()))
    header = (
        '---\n'
        '# Sonador integration modules, mounted with subPath over the paths the image\n'
        '# either misses or bakes in the wrong place. Sources live in docker/airflow/src.\n'
        'kind: ConfigMap\n'
        'apiVersion: v1\n'
        'metadata:\n'
        '  name: airflow-modules\n'
        '  namespace: airflow-ai\n'
        '  labels:\n'
        '    app.kubernetes.io/name: airflow\n'
        'data:\n')
    fresh.append(emit(os.path.join(HERE, 'airflow-modules.yaml'), [(header, keys)]))

    if CHECK_ONLY and not all(fresh):
        sys.exit(1)


if __name__ == '__main__':
    main()
