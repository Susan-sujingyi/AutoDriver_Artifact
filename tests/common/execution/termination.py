"""Classify trusted execution evidence, never hidden correctness feedback."""


def classify(result, events):
    if result.get('timed_out'):
        return {'kind': 'method_limit', 'reason': 'wall_time', 'retryable': False}
    if result.get('result', {}).get('status') == 'candidate_ready':
        return {'kind': 'completed', 'retryable': False}
    method_status=result.get('result',{}).get('status')
    if (result.get('status')=='pass' and result.get('result',{}).get('method')=='autodriver'
            and method_status in {'context_limit_exhausted','method_limit_exhausted'}):
        return {'kind':'method_limit','reason':method_status,'retryable':False}
    if (result.get('status')=='pass' and result.get('result',{}).get('method')=='autodriver'
            and method_status in {'upstream_no_candidate','upstream_obligations_violated','upstream_obligations_unresolved',
                'generation_no_candidate','candidate_tool_incomplete','candidate_compile_incomplete','candidate_compiled',
                'candidate_internal_checks_satisfied','candidate_checks_unknown','candidate_repair_bound_exhausted',
                'candidate_repair_response_rejected','candidate_repair_no_candidate','candidate_upstream_unresolved',
                'candidate_upstream_response_rejected','upstream_response_rejected','generation_response_rejected',
                'code_response_repair_exhausted'}):
        return {'kind':'completed','reason':method_status,'retryable':False}
    if result.get('status')=='pass' and method_status in ('public_compile_pass','public_compile_failed'):
        # Finishing the method's configured compile-repair loop is distinct
        # from passing the benchmark's independent native/behavior checks.
        return {'kind':'completed','reason':method_status,'retryable':False}
    for event in reversed(events):
        if event.get('event') == 'error':
            reason = event.get('reason', '')
            if (event.get('error_type') == 'BudgetDenied' and reason in (
                    'Episode deadline reached', 'Episode deadline reached before upstream dispatch',
                    'Original method deadline reached')):
                return {'kind':'method_limit','reason':'Episode deadline reached','retryable':False}
            if event.get('error_type') in ('ResponseProtocolError', 'UnicodeDecodeError') and event.get('phase') == 'response_receipt':
                return {'kind':'infrastructure', 'reason':'provider_protocol', 'retryable':True,
                        'recovery_mode':'inspect_receipt', 'new_generation_allowed':False,
                        'request_id':event.get('id'), 'operation':'response_receipt',
                        'error_type':event['error_type'],
                        'response_bytes_received':event.get('response_bytes_received')}
            local = {
                'request_context': ('request_context_failure', 'inspect_receipt'),
                'response_receipt': ('response_storage_failure', 'inspect_receipt'),
                'usage_settlement': ('response_settlement_failure', 'replay_response'),
                'delivery_claim': ('response_delivery_claim_failed', 'inspect_receipt'),
                'client_response': ('response_delivery_uncertain', 'inspect_client_checkpoint'),
                'response_replay': ('response_replay_refused', 'inspect_receipt'),
            }
            if event.get('phase') in local:
                code, mode = local[event['phase']]
                return {'kind': 'infrastructure', 'reason': code, 'retryable': True,
                        'recovery_mode': mode, 'new_generation_allowed': False,
                        'request_id': event.get('id'), 'operation': event['phase'],
                        'error_type': event.get('error_type'),
                        'response_bytes_received': event.get('response_bytes_received')}
            if reason == 'Insufficient stage budget for the next request reservation':
                return {'kind': 'infrastructure', 'reason': 'campaign_budget', 'retryable': True}
            if reason in ('Request-count limit reached', 'Episode deadline reached', 'Code repair limit reached','Candidate revision limit reached',
                          'Insufficient episode budget for the next request reservation'):
                return {'kind': 'method_limit', 'reason': reason, 'retryable': False}
            if event.get('provider_error_code') in ('insufficient_quota', 'insufficient_balance', 'rate_limit_exceeded'):
                return {'kind': 'infrastructure', 'reason': 'provider_quota', 'retryable': True}
            status = event.get('http_status')
            if (status in (401, 402, 403, 408, 409, 425, 429) or
                    isinstance(status, int) and status >= 500 or
                    event.get('phase') in ('upstream_headers', 'upstream_body') and
                    event.get('error_type') in ('URLError', 'TimeoutError', 'ConnectionError',
                                                'ConnectionResetError', 'OSError', 'IncompleteRead')):
                return {'kind': 'infrastructure', 'reason': 'provider_or_network',
                        'http_status': status, 'retryable': True,
                        'operation': event.get('phase'),
                        'error_type': event.get('error_type'),
                        'response_bytes_received': event.get('response_bytes_received')}
        if event.get('event') == 'response' and event.get('response_status') == 'failed':
            if event.get('terminal_error_code') in ('server_error', 'rate_limit_exceeded',
                                                    'insufficient_quota', 'insufficient_balance', 'overloaded'):
                return {'kind': 'infrastructure', 'reason': 'provider_terminal_failure', 'retryable': True}
        if event.get('event') == 'response' and event.get('response_status') is None:
            return {'kind': 'infrastructure', 'reason': 'provider_protocol', 'retryable': True}
    if result.get('reason') in ('KeyboardInterrupt', 'EvaluationInterrupted', 'SystemExit'):
        return {'kind': 'infrastructure', 'reason': 'interrupted', 'retryable': True}
    return {'kind': 'method_exit', 'retryable': False}
