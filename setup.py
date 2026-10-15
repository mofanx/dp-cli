# -*- coding:utf-8 -*-
"""构建期把 repo 根 skills/dp-cli/ 复制进 wheel 的 dp_cli/skill/。

skills/dp-cli/ 是 canonical 源（sync-skill.sh / test_docs 守卫 / skills.sh
生态均锚定 repo 路径，不动）；wheel 分发时随包携带，install --skills
从包内副本读取，版本与 CLI 同源。
"""
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPyWithSkill(build_py):
    def run(self):
        super().run()
        src = Path(__file__).parent / 'skills' / 'dp-cli'
        dst = Path(self.build_lib) / 'dp_cli' / 'skill'
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)


setup(cmdclass={'build_py': BuildPyWithSkill})
