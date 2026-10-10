"""Session commands use the same checkpoint implementation as MCP and HTTP."""
from .local import MAX_STL_BYTES, read_bytes, read_json
from .sessions import session_store


def add_commands(commands):
    sessions = commands.add_parser('session', help='Create, observe and control persistent design sessions.')
    actions = sessions.add_subparsers(dest='session_action', required=True)
    create = actions.add_parser('create')
    create.add_argument('--objective', required=True)
    create.add_argument('--project', default='My project')
    create.add_argument('--part', default='New part')
    create.add_argument('--mode', choices=('interactive', 'auto', 'teaching'), default='interactive')
    create.add_argument('--source', choices=('selected', 'copy', 'scratch'), default='copy')
    create.add_argument('--max-iterations', type=int, default=4)
    create.add_argument('--max-seconds', type=float, default=1800)
    create.add_argument('--max-tokens', type=int)
    create.add_argument('--stl')
    create.add_argument('--unit', choices=('mm', 'inch'), default='mm')
    actions.add_parser('list')
    for name in ('get', 'events', 'context', 'control', 'agent', 'signal', 'authorize', 'event', 'activity', 'begin', 'finish', 'test', 'fork', 'complete'):
        command = actions.add_parser(name)
        command.add_argument('session_id')
        if name == 'events':
            command.add_argument('--after', type=int, default=0)
        elif name == 'context':
            command.add_argument('--query', default='')
        elif name == 'control':
            command.add_argument('action', choices=('pause', 'interrupt', 'continue', 'feedback', 'settings', 'recover'))
            command.add_argument('--message', default='')
            command.add_argument('--settings', help='JSON settings file.')
        elif name == 'agent':
            command.add_argument('--name', required=True)
            command.add_argument('--controls', action='store_true', help='Declare support for MakerSim design pause/continue checkpoints.')
        elif name == 'signal':
            command.add_argument('run_id')
            command.add_argument('action', choices=('heartbeat', 'disconnect'))
            command.add_argument('--seen-sequence', type=int, help='Last event cursor read and handled; required to disconnect a client with window controls.')
        elif name == 'authorize':
            command.add_argument('run_id')
            command.add_argument('action', choices=('start', 'resume'))
            command.add_argument('--human-instruction', required=True, help='Relay the actual human instruction; never invent authorization.')
        elif name == 'event':
            command.add_argument('kind', choices=('plan', 'prediction', 'explanation', 'decision', 'lesson', 'checkpoint'))
            command.add_argument('--message', required=True)
            command.add_argument('--phase', choices=('plan', 'do', 'check', 'act'))
            command.add_argument('--data', help='JSON event details file.')
        elif name == 'activity':
            command.add_argument('kind', choices=('thinking', 'explaining', 'waiting', 'working'))
            command.add_argument('--message', required=True)
            command.add_argument('--operation-id')
        elif name == 'begin':
            command.add_argument('--description', required=True)
            command.add_argument('--phase', choices=('plan', 'do', 'check', 'act'), default='do')
        elif name == 'finish':
            command.add_argument('operation_id')
            command.add_argument('--explanation', required=True)
            command.add_argument('--stl')
            command.add_argument('--unit', choices=('mm', 'inch'), default='mm')
            command.add_argument('--revision')
            command.add_argument('--cad-reference')
            command.add_argument('--tokens-used', type=int, default=0)
            command.add_argument('--error')
        elif name == 'test':
            command.add_argument('--test', required=True)
            command.add_argument('--stl')
            command.add_argument('--revision')
            command.add_argument('--resolution', type=int, default=28)
            command.add_argument('--max-cells', type=int, default=4800)
        elif name == 'fork':
            command.add_argument('--sequence', type=int)
            command.add_argument('--objective')
        elif name == 'complete':
            command.add_argument('--summary', required=True)
        if name in ('event', 'activity', 'begin', 'test', 'complete'):
            command.add_argument('--run-id')


def execute(args):
    store = session_store()
    action = args.session_action
    if action == 'create':
        session = store.create({'objective': args.objective, 'project': args.project, 'part': args.part,
            'mode': args.mode, 'source': args.source, 'budgets': {'max_iterations': args.max_iterations,
            'max_seconds': args.max_seconds, 'max_tokens': args.max_tokens}})
        if args.stl:
            session = store.attach_baseline(session['id'], read_bytes(args.stl, MAX_STL_BYTES), args.unit)
        return session
    if action == 'list':
        return {'sessions': store.list('local-agent')}
    session_id = args.session_id
    if action == 'get':
        return store.get(session_id)
    if action == 'events':
        return store.events(session_id, args.after)
    if action == 'context':
        return store.context(session_id, args.query)
    if action == 'control':
        return store.control(session_id, args.action, args.message, read_json(args.settings) if args.settings else None)
    if action == 'agent':
        return store.connect_agent(session_id, args.name, args.controls)
    if action == 'signal':
        return store.agent_signal(session_id, args.run_id, args.action, args.seen_sequence)
    if action == 'authorize':
        return store.authorize_run(session_id, args.run_id, args.human_instruction, args.action)
    if action == 'event':
        return store.publish(session_id, args.kind, args.message, read_json(args.data) if args.data else None, args.phase, args.run_id)
    if action == 'begin':
        return store.begin_step(session_id, args.description, args.phase, run_id=args.run_id)
    if action == 'activity':
        return store.report_activity(session_id, args.kind, args.message, args.operation_id, args.run_id)
    if action == 'finish':
        return store.finish_step(session_id, args.operation_id, args.explanation, args.stl, args.unit,
                                 args.revision, args.cad_reference, args.tokens_used, args.error)
    if action == 'test':
        return store.run_simulation(session_id, args.test, args.stl, args.revision, args.resolution, args.max_cells, args.run_id)
    if action == 'fork':
        return store.fork(session_id, args.sequence, args.objective)
    return store.complete(session_id, args.summary, args.run_id)
