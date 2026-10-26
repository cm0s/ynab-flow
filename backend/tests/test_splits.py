"""Split validation: exact milliunit sums, signs, live categories, no transfers."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.categories import CategoryDirectory
from src.models import Base, Plan, CategoryGroup, Category
from src.splits import SplitError, SplitLine, unassigned, validate_lines


@pytest.fixture()
def directory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Plan(id="p", ynab_plan_id="yp", name="P"))
    db.add(CategoryGroup(id="g", plan_id="p", ynab_category_group_id="yg", name="G"))
    db.add_all([
        Category(id="groc", category_group_id="g", ynab_category_id="y-groc", name="Groceries"),
        Category(id="home", category_group_id="g", ynab_category_id="y-home", name="Household"),
        Category(id="gone", category_group_id="g", ynab_category_id="y-gone", name="Old", deleted=True),
    ])
    db.commit()
    yield CategoryDirectory(db, "p")
    db.close()


def lines(*pairs):
    return [SplitLine(amount_milliunits=a, category_id=c) for a, c in pairs]


def test_supermarket_split_is_valid(directory):
    validate_lines(lines((-62400, "groc"), (-22500, "home")), -84900, directory, currency="CHF")


def test_rounding_mismatch_reports_unassigned_amount(directory):
    split = lines((-62400, "groc"), (-22450, "home"))
    assert unassigned(split, -84900) == -50
    with pytest.raises(SplitError, match=r"CHF -84\.85.*CHF -84\.90: 0\.05 is unassigned"):
        validate_lines(split, -84900, directory, currency="CHF")


def test_over_assignment_is_named(directory):
    with pytest.raises(SplitError, match="over-assigned"):
        validate_lines(lines((-62400, "groc"), (-23000, "home")), -84900, directory)


def test_repayment_cannot_be_split(directory):
    with pytest.raises(SplitError, match="repayments cannot be split"):
        validate_lines(lines((-50000, "groc"), (-34900, "home")), -84900, directory, transfer_state="resolved")


@pytest.mark.parametrize("split, message", [
    (lines((-84900, "groc")), "at least two lines"),
    (lines((-90000, "groc"), (5100, "home")), "must be an outflow"),
    (lines((-84900, "groc"), (0, "home")), "must be an outflow"),
    (lines((-62400, "groc"), (-22500, "nope")), "choose a category"),
    (lines((-62400, "groc"), (-22500, "gone")), "was deleted"),
])
def test_invalid_splits(directory, split, message):
    with pytest.raises(SplitError, match=message):
        validate_lines(split, -84900, directory)


def test_inflow_split_needs_inflow_lines(directory):
    validate_lines(lines((20000, "groc"), (5000, "home")), 25000, directory)
    with pytest.raises(SplitError, match="must be an inflow"):
        validate_lines(lines((30000, "groc"), (-5000, "home")), 25000, directory)


def test_amounts_must_be_integer_milliunits(directory):
    with pytest.raises(SplitError, match="whole milliunits"):
        validate_lines([SplitLine(-62400.5, "groc"), SplitLine(-22499.5, "home")], -84900, directory)
