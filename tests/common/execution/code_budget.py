"""Validate explicit AutoDriver repair and candidate limits."""


def configured_limits(budget):
    keys={'code_repairs','candidate_checks'}
    if not keys.intersection(budget):return None
    if (not keys.issubset(budget) or type(budget['code_repairs']) is not int or
            type(budget['candidate_checks']) is not int or
            (budget['code_repairs'],budget['candidate_checks'])!=(4,5)):
        raise ValueError('The study code budget requires four repairs and five candidates')
    return {'code_repairs':4,'candidate_checks':5}

