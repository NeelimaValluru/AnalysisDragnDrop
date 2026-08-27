"""Code generation for previously silent analyzer/preprocessor variants."""

import re

from analysis_gui.pipeline import CodeGenerator, Node, PipelineGraph

PASSTHROUGH = re.compile(r"^output_0 = data$", re.M)


def _code_for(node: Node) -> str:
    graph = PipelineGraph()
    graph.add_node(node)
    return CodeGenerator(graph).generate()


def _assert_real_codegen(code: str, *needles: str) -> None:
    compile(code, "<generated>", "exec")
    assert PASSTHROUGH.search(code) is None, code
    for needle in needles:
        assert needle in code, f"expected {needle!r} in:\n{code}"


class TestFeatureSelect:
    def test_kbest_is_the_default(self):
        code = _code_for(Node.create_preprocessor("feature_select"))
        _assert_real_codegen(code, "SelectKBest", "f_classif")

    def test_kbest_honors_n_features(self):
        node = Node.create_preprocessor("feature_select")
        node.parameters["n_features"].value = 4
        code = _code_for(node)
        _assert_real_codegen(code, "int(4)")

    def test_variance_threshold(self):
        node = Node.create_preprocessor("feature_select")
        node.parameters["method"].value = "variance"
        code = _code_for(node)
        _assert_real_codegen(code, "VarianceThreshold")
        assert "SelectKBest" not in code


class TestRegression:
    def test_linear_is_the_default(self):
        code = _code_for(Node.create_analyzer("regression"))
        _assert_real_codegen(code, "LinearRegression", "reg.fit", "reg.predict")

    def test_ridge(self):
        node = Node.create_analyzer("regression")
        node.parameters["model_type"].value = "ridge"
        code = _code_for(node)
        _assert_real_codegen(code, "Ridge")
        assert "PolynomialFeatures" not in code

    def test_polynomial(self):
        node = Node.create_analyzer("regression")
        node.parameters["model_type"].value = "polynomial"
        code = _code_for(node)
        _assert_real_codegen(code, "PolynomialFeatures", "LinearRegression")


class TestClustering:
    def test_kmeans_is_unchanged(self):
        code = _code_for(Node.create_analyzer("clustering"))
        _assert_real_codegen(code, "KMeans", "n_clusters=3", "kmeans.fit_predict(data)")
        assert "DBSCAN" not in code
        assert "AgglomerativeClustering" not in code

    def test_dbscan(self):
        node = Node.create_analyzer("clustering")
        node.parameters["algorithm"].value = "dbscan"
        code = _code_for(node)
        _assert_real_codegen(code, "DBSCAN", "eps=0.5", "min_samples=5")
        assert "kmeans.fit_predict" not in code

    def test_dbscan_honors_eps_and_min_samples(self):
        node = Node.create_analyzer("clustering")
        node.parameters["algorithm"].value = "dbscan"
        node.parameters["eps"].value = 1.25
        node.parameters["min_samples"].value = 8
        code = _code_for(node)
        _assert_real_codegen(code, "eps=1.25", "min_samples=8")

    def test_hierarchical(self):
        node = Node.create_analyzer("clustering")
        node.parameters["algorithm"].value = "hierarchical"
        node.parameters["n_clusters"].value = 5
        code = _code_for(node)
        _assert_real_codegen(
            code, "AgglomerativeClustering", "n_clusters=5", "agg.fit_predict"
        )


class TestPreprocessorOptions:
    def test_robust_normalize(self):
        node = Node.create_preprocessor("normalize")
        node.parameters["method"].value = "robust"
        code = _code_for(node)
        _assert_real_codegen(code, ".median()", ".quantile(0.75)")

    def test_median_missing(self):
        node = Node.create_preprocessor("handle_missing")
        node.parameters["strategy"].value = "median"
        code = _code_for(node)
        _assert_real_codegen(code, ".fillna(", ".median()")

    def test_forward_fill_missing(self):
        node = Node.create_preprocessor("handle_missing")
        node.parameters["strategy"].value = "forward_fill"
        code = _code_for(node)
        _assert_real_codegen(code, ".ffill()")
