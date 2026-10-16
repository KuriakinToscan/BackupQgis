# -*- coding: utf-8 -*-

import pytest

from core.dependencies import (DEP_QGIS, DEP_PYTHON, strip_qgis_spec, strip_python_spec,
                               extract_plugin_dependencies, import_name_for, find_dependencies)


@pytest.mark.parametrize('spec, expected', [
    ('requests', 'requests'),
    ('requests>=2.20', 'requests'),
    ('numpy==1.26.4', 'numpy'),
    ('pandas<3', 'pandas'),
    ('scipy >1.0', 'scipy'),
    ('  shapely  ', 'shapely'),
])
def test_strip_python_spec(spec, expected):
    assert strip_python_spec(spec) == expected


@pytest.mark.parametrize('spec, expected', [
    ('QuickOSM', 'QuickOSM'),
    ('QuickOSM==2.2.3', 'QuickOSM'),
    ('Lat Lon Tools>=3.0', 'Lat Lon Tools'),
])
def test_strip_qgis_spec_keeps_spaces(spec, expected):
    assert strip_qgis_spec(spec) == expected


def write_plugin(tmp_path, metadata=None, requirements=None):
    plugin_dir = tmp_path / 'plugin'
    plugin_dir.mkdir()
    if metadata is not None:
        (plugin_dir / 'metadata.txt').write_text(metadata, encoding='utf-8')
    if requirements is not None:
        (plugin_dir / 'requirements.txt').write_text(requirements, encoding='utf-8')
    return str(plugin_dir)


def test_extract_from_metadata_and_requirements(tmp_path):
    plugin_dir = write_plugin(
        tmp_path,
        metadata=(
            '[general]\n'
            'name=Teste\n'
            'plugin_dependencies=QuickOSM==2.0, Lat Lon Tools\n'
            'python_dependencies=requests>=2.0, pyyaml\n'
        ),
        requirements='# comentário\n\nnumpy==1.26\nrequests\n',
    )
    deps = extract_plugin_dependencies(plugin_dir)
    assert sorted(deps[DEP_QGIS]) == ['Lat Lon Tools', 'QuickOSM']
    assert sorted(deps[DEP_PYTHON]) == ['numpy', 'pyyaml', 'requests']


def test_extract_without_files(tmp_path):
    assert extract_plugin_dependencies(write_plugin(tmp_path)) == {DEP_QGIS: [], DEP_PYTHON: []}


def test_extract_ignores_broken_metadata(tmp_path):
    plugin_dir = write_plugin(tmp_path, metadata='isto não é ini', requirements='lxml\n')
    assert extract_plugin_dependencies(plugin_dir) == {DEP_QGIS: [], DEP_PYTHON: ['lxml']}


def test_import_name_for():
    assert import_name_for('PyYAML') == 'yaml'
    assert import_name_for('requests') == 'requests'


def test_find_dependencies_only_active_plugins(tmp_path):
    active_dir = write_plugin(
        tmp_path,
        metadata='[general]\nname=A\nplugin_dependencies=present, absent\n',
        requirements='okpkg\nmissingpkg\n',
    )
    plugins = [
        {'key': 'a', 'name': 'Plugin A', 'active': True, 'path': active_dir},
        {'key': 'b', 'name': 'Plugin B', 'active': False, 'path': active_dir},
    ]
    deps = find_dependencies(plugins, ['a', 'present'], python_checker=lambda name: name == 'okpkg')

    summary = sorted((d['source'], d['kind'], d['name'], d['is_missing']) for d in deps)
    assert summary == [
        ('Plugin A', DEP_PYTHON, 'missingpkg', True),
        ('Plugin A', DEP_PYTHON, 'okpkg', False),
        ('Plugin A', DEP_QGIS, 'absent', True),
        ('Plugin A', DEP_QGIS, 'present', False),
    ]
