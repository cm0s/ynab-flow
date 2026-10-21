import numpy as np
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src import ml_classifier
from src.ml_classifier import MLClassifier
from src.models import Base, Plan, Account, Payee, CategoryGroup, Category, Transaction


@pytest.fixture
def training_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(ml_classifier, 'MODEL_DIR', tmp_path)
    monkeypatch.setattr(ml_classifier, 'PAYEE_MODEL_PATH', tmp_path / 'payee.joblib')
    monkeypatch.setattr(ml_classifier, 'CATEGORY_MODEL_PATH', tmp_path / 'category.joblib')
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        plan = Plan(name='Budget', ynab_plan_id='budget')
        other = Plan(name='Other', ynab_plan_id='other')
        db.add_all([plan, other])
        db.flush()
        account = Account(plan_id=plan.id, ynab_account_id='account', name='Bank', type='checking')
        group = CategoryGroup(plan_id=plan.id, ynab_category_group_id='group', name='Expenses')
        payees = [Payee(plan_id=plan.id, ynab_payee_id=str(i), name=f'Shop {i}') for i in range(2)]
        db.add_all([account, group, *payees])
        db.flush()
        cats = [Category(category_group_id=group.id, ynab_category_id=str(i), name=f'Category {i}') for i in range(2)]
        db.add_all(cats)
        db.flush()

        def add(memo, payee=None, category=None, **kwargs):
            txn = Transaction(plan_id=kwargs.pop('plan_id', plan.id), account_id=account.id,
                              ynab_transaction_id=str(db.query(Transaction).count()), date='2026-01-01',
                              amount=-1000, memo=memo, payee_id=payee.id if payee else None,
                              category_id=category.id if category else None, **kwargs)
            db.add(txn)
            db.flush()
        yield db, plan, other, payees, cats, add
    engine.dispose()


def test_training_keeps_labels_aligned_and_queries_once(training_setup, monkeypatch):
    db, plan, other, payees, cats, add = training_setup
    add('CATEGORY ONLY', category=cats[0])
    add('PAYEE ONLY', payee=payees[0])
    add('BOTH LABELS', payee=payees[1], category=cats[1])
    add('DELETED', payee=payees[0], category=cats[0], deleted=True)
    add('OTHER PLAN', payee=payees[0], category=cats[0], plan_id=other.id)
    db.commit()
    plan_id = plan.id
    captured = []

    class RecordingPipeline:
        def fit(self, texts, labels):
            captured.append(list(zip(texts, labels)))

    classifier = MLClassifier()
    monkeypatch.setattr(classifier, '_build_pipeline', RecordingPipeline)
    monkeypatch.setattr(classifier, '_save_models', lambda: None)
    selects = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT'):
            selects.append(statement)

    event.listen(db.bind, 'before_cursor_execute', record)
    try:
        stats = classifier.train(db, plan_id)
    finally:
        event.remove(db.bind, 'before_cursor_execute', record)
    assert len(selects) == 1
    assert stats['training_rows'] == 3
    clean = lambda text: ml_classifier.normalize(text).cleaned_memo
    assert captured == [[(clean('PAYEE ONLY'), 'Shop 0'), (clean('BOTH LABELS'), 'Shop 1')],
                        [(clean('CATEGORY ONLY'), cats[0].id), (clean('BOTH LABELS'), cats[1].id)]]  # stable ids


def test_sparse_float32_training_persists_probabilities(training_setup):
    db, plan, _, payees, cats, add = training_setup
    for _ in range(5):
        add('MIGROS GROCERIES', payees[0], cats[0])
        add('SBB TRAIN TICKET', payees[1], cats[1])
    db.commit()
    classifier = MLClassifier()
    stats = classifier.train(db, plan.id)
    assert stats['payee_trained'] and stats['category_trained']
    for pipeline in [classifier.payee_pipeline, classifier.category_pipeline]:
        features = pipeline['tfidf'].transform(['MIGROS GROCERIES'])
        assert features.dtype == np.float32
        assert pipeline['clf'].coef_.dtype == np.float32
        probabilities = pipeline.predict_proba(['MIGROS GROCERIES'])
        assert np.isfinite(probabilities).all()
        assert probabilities.sum() == pytest.approx(1.0)
    loaded = MLClassifier()
    assert loaded.predict('MIGROS GROCERIES').payee == classifier.predict('MIGROS GROCERIES').payee
    assert loaded.predict('SBB TRAIN TICKET').category == classifier.predict('SBB TRAIN TICKET').category


def test_model_dir_keeps_default_models_untouched(training_setup, tmp_path_factory):
    db, plan, _, payees, cats, add = training_setup
    for i in range(6):
        add(f'SHOP {i % 2} PURCHASE {i}', payee=payees[i % 2], category=cats[i % 2])
    db.commit()
    default_dir = ml_classifier.MODEL_DIR          # the fixture's stand-in for backend/models
    before = sorted(p.name for p in default_dir.iterdir())
    other = tmp_path_factory.mktemp('evaluation') / 'models'
    stats = MLClassifier(model_dir=other).train(db, plan.id)
    assert stats['payee_trained'] and stats['category_trained']
    assert sorted(p.name for p in other.iterdir()) == sorted([ml_classifier.CATEGORY_MODEL_PATH.name, ml_classifier.PAYEE_MODEL_PATH.name])
    assert sorted(p.name for p in default_dir.iterdir()) == before
    reloaded = MLClassifier(model_dir=other)
    assert reloaded.payee_pipeline is not None and reloaded.category_pipeline is not None


def test_machine_guesses_and_split_parents_are_not_learned(training_setup, monkeypatch):
    from src.historical_matcher import HistoricalMatcher
    db, plan, _, payees, cats, add = training_setup
    add('TRUSTED SHOP', payee=payees[0], category=cats[0])
    add('APP CONFIRMED', payee=payees[0], category=cats[0], provenance='app_confirmed')
    add('MACHINE GUESS', payee=payees[1], category=cats[1], provenance='app_unreviewed', import_id='YF1:x:1')
    add('SPLIT PARENT', payee=payees[1], category=cats[1], provenance='split_parent', is_split=True)
    add('DELETED', payee=payees[0], category=cats[0], deleted=True)
    add('', payee=payees[0], category=cats[0])
    db.commit()
    captured = []

    class RecordingPipeline:
        def fit(self, texts, labels):
            captured.append(texts)

    classifier = MLClassifier()
    monkeypatch.setattr(classifier, '_build_pipeline', RecordingPipeline)
    monkeypatch.setattr(classifier, '_save_models', lambda: None)
    stats = classifier.train(db, plan.id)
    learned = {t for texts in captured for t in texts}
    clean = lambda text: ml_classifier.normalize(text).cleaned_memo
    assert clean('MACHINE GUESS') not in learned and clean('SPLIT PARENT') not in learned
    assert stats['training_rows'] == 2
    assert stats['excluded'] == {'app_unreviewed': 1, 'deleted': 1, 'no_description': 1, 'split_parent': 1}
    history = HistoricalMatcher(db, plan.id)
    assert history.match(clean('MACHINE GUESS'), '') is None or history.match(clean('MACHINE GUESS'), '').source_type != 'exact'
    assert history.match(clean('TRUSTED SHOP'), '').source_type == 'exact'
