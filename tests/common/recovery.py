"""Recovery concerns controller interruption, not candidate observation limits.

Keep benchmark verdicts untouched. A normally returned not_completed verdict
is a terminal, nonpassing observation; it is not evidence of a lost host run.
"""
POLICY = 'autodriver.recovery/v2'


def evaluation_recovery(report, *, interrupted=False, exception=False, mode='evaluate'):
    status = report['status']
    reason = str(report.get('reason', ''))
    explicit_interrupt = (interrupted or report.get('behavior', {}).get('status') == 'interrupted'
                          or reason.split(':', 1)[0] in
                          ('KeyboardInterrupt', 'EvaluationInterrupted', 'SystemExit'))
    if status == 'infrastructure_error':
        retryable, cause = True, 'external_interruption' if explicit_interrupt else 'infrastructure_failure'
    elif status == 'not_completed' and (explicit_interrupt or exception):
        retryable, cause = True, 'external_interruption' if explicit_interrupt else 'controller_exception'
    elif status == 'not_completed':
        retryable, cause = False, 'observation_or_execution_limit'
    else:
        retryable, cause = False, 'terminal_result'
    return {'policy': POLICY, 'retryable': retryable, 'mode': mode, 'reason': cause}


def archived_recovery(report):
    """Interpret r5's overly broad flag without changing archived evidence."""
    saved = report.get('recovery')
    if saved and saved.get('policy') == POLICY:
        return saved
    if report['status'] == 'not_completed':
        return evaluation_recovery(report, mode=(saved or {}).get('mode', 'evaluate'))
    return saved
