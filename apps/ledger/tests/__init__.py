from apps.ledger.models import Account, AccountType, CommodityScope, Nature


def money_account() -> Account:
    """A postable asset account taking any currency (tests of the posting engine)."""
    account, _created = Account.objects.get_or_create(
        code="1190", defaults={"name": "Test money", "type": AccountType.ASSET,
                               "nature": Nature.DEBIT, "commodity_scope": CommodityScope.MONEY,
                               "parent": Account.objects.get(code="11")})
    return account
