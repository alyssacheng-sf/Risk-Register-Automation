"""Tests for the risk analyzer (staleness, categorization, prioritization)."""

from datetime import date, timedelta

import pytest

from src.models.risk import Risk
from src.risk_analyzer import RiskAnalyzer, AnalysisResult, load_categories


# --- Test Fixtures ---

def make_risk(
    id="a1jTEST",
    name="Test Risk",
    status="Open",
    impact="High",
    probability="Medium",
    last_reviewed_date=None,
    identified_on=None,
    team_name="MC Test",
    owner_email="test@salesforce.com",
    details=None,
    **kwargs,
):
    """Helper to create a Risk with sensible defaults."""
    return Risk(
        id=id,
        name=name,
        status=status,
        impact=impact,
        probability=probability,
        last_reviewed_date=last_reviewed_date,
        identified_on=identified_on,
        team_name=team_name,
        owner_email=owner_email,
        details=details,
        **kwargs,
    )


@pytest.fixture
def analyzer():
    """Analyzer with default categories."""
    return RiskAnalyzer()


@pytest.fixture
def sample_risks():
    """A diverse set of risks for testing."""
    today = date.today()
    return [
        # Stale, high impact (8 days since review, threshold = 7)
        make_risk(
            id="r1",
            name="Auth service JWT token expiry",
            impact="High",
            probability="High",
            last_reviewed_date=today - timedelta(days=8),
            owner_email="alice@salesforce.com",
            team_name="MC CIAM",
        ),
        # Stale, medium impact (20 days, threshold = 14)
        make_risk(
            id="r2",
            name="Database connection pool exhaustion",
            impact="Medium",
            probability="Medium",
            last_reviewed_date=today - timedelta(days=20),
            owner_email="bob@salesforce.com",
            team_name="MC Data Services",
            details="Connection pool running out under high load",
        ),
        # NOT stale, high impact (5 days, threshold = 7)
        make_risk(
            id="r3",
            name="API rate limiting bypass",
            impact="High",
            probability="Low",
            last_reviewed_date=today - timedelta(days=5),
            owner_email="alice@salesforce.com",
            team_name="MC API Frameworks",
        ),
        # Stale, low impact (35 days, threshold = 30)
        make_risk(
            id="r4",
            name="Email template rendering slow on large sends",
            impact="Low",
            probability="Medium",
            last_reviewed_date=today - timedelta(days=35),
            owner_email="carol@salesforce.com",
            team_name="MC Email Apps",
        ),
        # Stale, no review date at all
        make_risk(
            id="r5",
            name="Network firewall rules need update",
            impact="High",
            probability="Medium",
            last_reviewed_date=None,
            identified_on=today - timedelta(days=100),
            owner_email="bob@salesforce.com",
            team_name="SFMC Network Security Operations",
            details="Firewall rules outdated, DNS changes needed",
        ),
    ]


# --- Categorization Tests ---

class TestCategorization:
    """Test keyword-based risk categorization."""

    def test_security_category(self, analyzer):
        risk = make_risk(name="JWT token authentication vulnerability")
        cats = analyzer.categorize(risk)
        assert "security" in cats

    def test_performance_category(self, analyzer):
        risk = make_risk(name="High latency on API calls causing timeout")
        cats = analyzer.categorize(risk)
        assert "performance" in cats

    def test_reliability_category(self, analyzer):
        risk = make_risk(name="Service outage due to single point of failure")
        cats = analyzer.categorize(risk)
        assert "reliability" in cats

    def test_scalability_category(self, analyzer):
        risk = make_risk(name="Cannot handle high volume traffic burst")
        cats = analyzer.categorize(risk)
        assert "scalability" in cats

    def test_data_category(self, analyzer):
        risk = make_risk(name="Database migration may cause data loss")
        cats = analyzer.categorize(risk)
        assert "data" in cats

    def test_infrastructure_category(self, analyzer):
        risk = make_risk(name="Network load balancer configuration issue")
        cats = analyzer.categorize(risk)
        assert "infrastructure" in cats

    def test_messaging_category(self, analyzer):
        risk = make_risk(name="Email delivery queue backlog")
        cats = analyzer.categorize(risk)
        assert "messaging" in cats

    def test_multiple_categories(self, analyzer):
        risk = make_risk(
            name="Database timeout causing outage",
            details="Connection pool exhaustion leads to service downtime",
        )
        cats = analyzer.categorize(risk)
        # Should match both performance (timeout) and reliability (outage/downtime)
        assert len(cats) >= 2

    def test_uncategorized_fallback(self, analyzer):
        risk = make_risk(name="Miscellaneous unknown thing")
        cats = analyzer.categorize(risk)
        assert cats == ["uncategorized"]

    def test_matches_in_details_field(self, analyzer):
        risk = make_risk(name="General issue", details="Authentication credentials exposed")
        cats = analyzer.categorize(risk)
        assert "security" in cats

    def test_case_insensitive(self, analyzer):
        risk = make_risk(name="JWT AUTHENTICATION FAILURE")
        cats = analyzer.categorize(risk)
        assert "security" in cats

    def test_custom_categories(self):
        """Should work with custom category definitions."""
        custom_cats = {"custom": ["unicorn", "rainbow"]}
        analyzer = RiskAnalyzer(categories=custom_cats)
        risk = make_risk(name="A wild unicorn appeared")
        cats = analyzer.categorize(risk)
        assert "custom" in cats


# --- Analysis Tests ---

class TestAnalysis:
    """Test the full analysis pipeline."""

    def test_basic_analysis(self, analyzer, sample_risks):
        result = analyzer.analyze(sample_risks)
        assert isinstance(result, AnalysisResult)
        assert result.total_open == 5
        assert result.total_stale == 4  # r1, r2, r4, r5 are stale
        assert result.total_current == 1  # r3 is current

    def test_prioritization_order(self, analyzer, sample_risks):
        result = analyzer.analyze(sample_risks)
        # Highest risk_score should be first
        scores = [r.risk_score for r in result.prioritized]
        assert scores == sorted(scores, reverse=True)

    def test_grouped_by_owner(self, analyzer, sample_risks):
        result = analyzer.analyze(sample_risks)
        assert "alice@salesforce.com" in result.by_owner
        assert "bob@salesforce.com" in result.by_owner
        # Alice has 1 stale risk (r1); r3 is not stale
        assert len(result.by_owner["alice@salesforce.com"]) == 1
        # Bob has 2 stale risks (r2, r5)
        assert len(result.by_owner["bob@salesforce.com"]) == 2

    def test_grouped_by_team(self, analyzer, sample_risks):
        result = analyzer.analyze(sample_risks)
        assert "MC CIAM" in result.by_team
        assert "MC Data Services" in result.by_team

    def test_grouped_by_category(self, analyzer, sample_risks):
        result = analyzer.analyze(sample_risks)
        # r1 mentions JWT/auth → security
        assert "security" in result.by_category

    def test_compliance_rate(self, analyzer, sample_risks):
        result = analyzer.analyze(sample_risks)
        # 1 out of 5 is current
        assert result.compliance_rate == pytest.approx(0.2)
        assert result.compliance_rate_pct == "20%"

    def test_empty_list(self, analyzer):
        result = analyzer.analyze([])
        assert result.total_open == 0
        assert result.total_stale == 0
        assert result.compliance_rate == 1.0

    def test_all_current(self, analyzer):
        risks = [
            make_risk(
                id=f"r{i}",
                impact="High",
                last_reviewed_date=date.today() - timedelta(days=1),
            )
            for i in range(5)
        ]
        result = analyzer.analyze(risks)
        assert result.total_stale == 0
        assert result.compliance_rate == 1.0


# --- Escalation Tests ---

class TestEscalation:
    """Test escalation candidate detection."""

    def test_escalation_high_impact_stale(self, analyzer):
        risk = make_risk(
            impact="High",
            last_reviewed_date=date.today() - timedelta(days=10),
        )
        candidates = analyzer.get_escalation_candidates([risk])
        assert len(candidates) == 1

    def test_no_escalation_if_not_stale(self, analyzer):
        risk = make_risk(
            impact="High",
            last_reviewed_date=date.today() - timedelta(days=3),
        )
        candidates = analyzer.get_escalation_candidates([risk])
        assert len(candidates) == 0

    def test_escalation_long_overdue(self, analyzer):
        """Medium risk stale well past threshold + response window."""
        risk = make_risk(
            impact="Medium",
            last_reviewed_date=date.today() - timedelta(days=30),
        )
        candidates = analyzer.get_escalation_candidates([risk], days_without_response=14)
        # 30 days > 14 (threshold) + 14 (response) = 28
        assert len(candidates) == 1

    def test_no_escalation_medium_slightly_stale(self, analyzer):
        """Medium risk barely stale shouldn't escalate."""
        risk = make_risk(
            impact="Medium",
            last_reviewed_date=date.today() - timedelta(days=16),
        )
        candidates = analyzer.get_escalation_candidates([risk], days_without_response=14)
        # 16 days < 14 + 14 = 28, and impact is not High
        assert len(candidates) == 0


# --- Newly Stale Tests ---

class TestNewlyStale:
    """Test detection of risks that just became stale."""

    def test_finds_newly_stale_high_impact(self, analyzer):
        """Risk that crossed the 7-day threshold today."""
        risk = make_risk(
            impact="High",
            last_reviewed_date=date.today() - timedelta(days=8),
        )
        newly = analyzer.get_newly_stale([risk], since_days=1)
        assert len(newly) == 1

    def test_ignores_long_stale(self, analyzer):
        """Risk that's been stale for weeks shouldn't be 'newly' stale."""
        risk = make_risk(
            impact="High",
            last_reviewed_date=date.today() - timedelta(days=30),
        )
        newly = analyzer.get_newly_stale([risk], since_days=1)
        assert len(newly) == 0

    def test_ignores_current(self, analyzer):
        """Current risk shouldn't show up."""
        risk = make_risk(
            impact="High",
            last_reviewed_date=date.today() - timedelta(days=3),
        )
        newly = analyzer.get_newly_stale([risk], since_days=1)
        assert len(newly) == 0


# --- Config Loading Tests ---

class TestConfigLoading:

    def test_load_categories_from_file(self):
        categories = load_categories()
        assert "security" in categories
        assert "performance" in categories
        assert "reliability" in categories
        assert len(categories) >= 7
        # Each category should have keywords
        for name, keywords in categories.items():
            assert len(keywords) > 0
            # All keywords should be lowercase
            for kw in keywords:
                assert kw == kw.lower()
