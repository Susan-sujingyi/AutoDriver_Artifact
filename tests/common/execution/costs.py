"""Standalone trials build fresh FP/DBC/BP; all costs are charged online."""


def trial_cost_columns(configuration, trial, usage):
    return {f'total_known_cost_{bound}_cny': usage.get(f'cost_{bound}_cny', '')
            for bound in ('lower', 'upper')}
