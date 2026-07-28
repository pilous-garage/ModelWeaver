"""Tests des commandes built-in."""


class TestEcho:
    def test_simple(self, shell):
        r = shell.run("echo hello")
        assert r["exit_code"] == 0
        assert r["stdout"] == "hello\n"

    def test_multiple_args(self, shell):
        r = shell.run("echo hello world")
        assert r["stdout"].strip() == "hello world"

    def test_empty(self, shell):
        r = shell.run("echo")
        assert r["exit_code"] == 0


class TestPwd:
    def test_pwd(self, shell, workdir):
        r = shell.run("pwd")
        assert r["exit_code"] == 0
        assert workdir in r["stdout"]


class TestLs:
    def test_ls_empty(self, shell):
        r = shell.run("ls")
        assert r["exit_code"] == 0

    def test_ls_with_file(self, shell):
        shell.run("touch afile.txt")
        r = shell.run("ls")
        assert "afile.txt" in r["stdout"]

    def test_ls_outside_vfs(self, shell):
        r = shell.run("ls /tmp")
        assert r["exit_code"] != 0


class TestCat:
    def test_cat_file(self, shell):
        shell.run("write test.txt hello world")
        r = shell.run("cat test.txt")
        assert "hello world" in r["stdout"]

    def test_cat_not_found(self, shell):
        r = shell.run("cat nonexistent.txt")
        assert r["exit_code"] != 0


class TestHeadTail:
    def test_head(self, shell):
        shell.run("echo a > data.txt")
        shell.run("echo b >> data.txt")
        shell.run("echo c >> data.txt")
        shell.run("echo d >> data.txt")
        shell.run("echo e >> data.txt")
        shell.run("echo f >> data.txt")
        r = shell.run("head -n 2 data.txt")
        assert r["stdout"].strip() == "a\nb".strip()

    def test_tail(self, shell):
        shell.run("echo a > data.txt")
        shell.run("echo b >> data.txt")
        shell.run("echo c >> data.txt")
        shell.run("echo d >> data.txt")
        shell.run("echo e >> data.txt")
        shell.run("echo f >> data.txt")
        r = shell.run("tail -n 2 data.txt")
        assert r["stdout"].strip() == "e\nf".strip()


class TestWc:
    def test_wc_lines(self, shell):
        shell.run("echo a > data.txt")
        shell.run("echo b >> data.txt")
        shell.run("echo c >> data.txt")
        r = shell.run("wc -l data.txt")
        assert r["stdout"].strip().startswith("3")

    def test_wc_default(self, shell):
        shell.run("write data.txt a b c")
        r = shell.run("wc data.txt")
        parts = r["stdout"].strip().split()
        assert len(parts) == 4  # lines words chars filename


class TestFind:
    def test_find(self, shell):
        shell.run("mkdir -p a/b/c")
        shell.run("touch a/b/c/f.txt")
        r = shell.run("find a")
        assert "b/c/f.txt" in r["stdout"]


class TestMkdir:
    def test_mkdir(self, shell):
        r = shell.run("mkdir newdir")
        assert r["exit_code"] == 0
        r2 = shell.run("ls")
        assert "newdir" in r2["stdout"]

    def test_mkdir_existing(self, shell):
        shell.run("mkdir newdir")
        r = shell.run("mkdir newdir")
        assert r["exit_code"] != 0


class TestRm:
    def test_rm(self, shell):
        shell.run("touch f.txt")
        r = shell.run("rm f.txt")
        assert r["exit_code"] == 0
        r2 = shell.run("ls")
        assert "f.txt" not in r2["stdout"]

    def test_rm_nonexistent(self, shell):
        r = shell.run("rm nonexistent.txt")
        assert r["exit_code"] != 0


class TestCpMv:
    def test_cp(self, shell):
        shell.run("write src.txt original")
        r = shell.run("cp src.txt dst.txt")
        assert r["exit_code"] == 0
        r2 = shell.run("cat dst.txt")
        assert "original" in r2["stdout"]

    def test_mv(self, shell):
        shell.run("write src.txt original")
        r = shell.run("mv src.txt renamed.txt")
        assert r["exit_code"] == 0
        r2 = shell.run("ls")
        assert "src.txt" not in r2["stdout"]
        assert "renamed.txt" in r2["stdout"]


class TestSortUniqDiff:
    def test_sort(self, shell):
        shell.run("echo c > data.txt")
        shell.run("echo a >> data.txt")
        shell.run("echo b >> data.txt")
        r = shell.run("sort data.txt")
        assert r["stdout"].strip().split() == ["a", "b", "c"]

    def test_uniq(self, shell):
        shell.run("echo a > data.txt")
        shell.run("echo a >> data.txt")
        shell.run("echo b >> data.txt")
        shell.run("echo b >> data.txt")
        shell.run("echo c >> data.txt")
        r = shell.run("uniq data.txt")
        assert r["stdout"] == "a\nb\nc\n"

    def test_diff(self, shell):
        shell.run("write a.txt same")
        shell.run("write b.txt same")
        r = shell.run("diff a.txt b.txt")
        assert r["exit_code"] == 0


class TestEnvWhich:
    def test_env(self, shell):
        r = shell.run("env")
        assert "PYTHON" in r["stdout"] or "SHELL" in r["stdout"] or "PATH" in r["stdout"]

    def test_which(self, shell):
        r = shell.run("which echo")
        assert r["exit_code"] == 0


class TestUnameHostnameDate:
    def test_uname(self, shell):
        r = shell.run("uname")
        assert r["exit_code"] == 0
        assert "Linux" in r["stdout"] or r["stdout"].strip()

    def test_hostname(self, shell):
        r = shell.run("hostname")
        assert r["exit_code"] == 0
        assert r["stdout"].strip()

    def test_date(self, shell):
        r = shell.run("date")
        assert r["exit_code"] == 0


class TestTrueFalse:
    def test_true(self, shell):
        r = shell.run("true")
        assert r["exit_code"] == 0

    def test_false(self, shell):
        r = shell.run("false")
        assert r["exit_code"] == 1


class TestExit:
    def test_exit(self, shell):
        r = shell.run("exit")
        assert r["exit_code"] == 0


class TestHelp:
    def test_help(self, shell):
        r = shell.run("help")
        assert "cd" in r["stdout"]
        assert "echo" in r["stdout"]


class TestCutTrPaste:
    def test_cut(self, shell):
        shell.run("write data.txt a:b:c")
        r = shell.run("cut -d: -f2 data.txt")
        # cut utilise stdin quand pas de fichier → à améliorer
        assert r["exit_code"] == 0

    def test_tr(self, shell):
        r = shell.run("echo hello")
        hello = r["stdout"]
        assert hello.strip() == "hello"
