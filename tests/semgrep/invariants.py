"""Non-executed examples used exclusively by Semgrep rule tests."""

import ast
import gzip
import json
import pickle
import shutil

import yaml


def parsing(payload):
    # ruleid: no-executable-workout-parsing
    eval(payload)
    # ruleid: no-executable-workout-parsing
    exec(payload)
    # ruleid: no-unsafe-deserialization
    pickle.loads(payload)
    # ruleid: no-unsafe-deserialization
    yaml.unsafe_load(payload)
    # ruleid: no-unsafe-deserialization
    yaml.load(payload)
    # ok: no-executable-workout-parsing
    ast.literal_eval(payload)
    # ok: no-executable-workout-parsing
    json.loads(payload)
    # ok: no-unsafe-deserialization
    yaml.safe_load(payload)


def archive_reads(path):
    with gzip.open(path, "rb") as archive:
        # ruleid: no-unbounded-archive-read
        archive.read()
        # ruleid: no-unbounded-archive-read
        archive.readlines()
        # ruleid: no-unbounded-archive-read
        archive.read(-1)
        # ok: no-unbounded-archive-read
        archive.read(8192)
        # ok: no-unbounded-archive-read
        for line in archive:
            print(line)


def extraction(archive, destination):
    # ruleid: no-unsafe-archive-extraction
    archive.extractall(destination)
    # ruleid: no-unsafe-archive-extraction
    shutil.unpack_archive(archive, destination)


def in_place(source):
    # ruleid: no-in-place-workout-output
    with source.open("rb") as incoming, source.open("wb") as outgoing:
        outgoing.write(incoming.read(8192))
    # ok: no-in-place-workout-output
    with source.open("rb") as incoming, source.with_suffix(".tmp").open(
        "wb"
    ) as outgoing:
        outgoing.write(incoming.read(8192))
