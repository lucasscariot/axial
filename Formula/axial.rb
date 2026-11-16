class Axial < Formula
  desc "Dashboards as code for BigQuery and DuckDB, with per-query cost tracing"
  homepage "https://github.com/lucasscariot/axial"
  url "https://github.com/lucasscariot/axial/archive/d96ab89f2d11a6e9e5c961bd0245b5a0cd0e259a.tar.gz"
  version "0.1.2"
  sha256 "ebbe4800ca580f891e2e2183020ce385de5038aea6f9c4610351853de4feb2e5"

  depends_on "uv" => :build
  depends_on "python@3.14"

  def install
    # This tap uses the release's uv.lock, including its artifact checksums.
    # Keep the environment inside the keg and use Homebrew's Python.
    ENV["UV_PYTHON_DOWNLOADS"] = "never"
    ENV["UV_CACHE_DIR"] = buildpath/"uv-cache"
    ENV["UV_PROJECT_ENVIRONMENT"] = libexec/"venv"
    libexec.install "pyproject.toml", "uv.lock", "src"
    cd libexec do
      system formula_opt_bin("uv")/"uv", "sync", "--frozen", "--no-dev",
             "--no-editable", "--extra", "duckdb", "--no-managed-python",
             "--python", formula_opt_bin("python@3.14")/"python3.14"
    end
    bin.install_symlink libexec/"venv/bin/axial"
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/axial --version")
    system bin/"axial", "init", "demo", "--duckdb", "demo.duckdb"
    cd "demo" do
      system bin/"axial", "check"
    end
    system libexec/"venv/bin/python", "-c", <<~PYTHON
      import duckdb
      from axial.app import WEB_DIST
      assert (WEB_DIST / "index.html").is_file()
      assert duckdb.connect(":memory:").execute("SELECT 42").fetchone() == (42,)
    PYTHON
  end
end
