# Copyright 2020-2021 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Simulator class in pure Python to pseudorandomly generate happy-path valid
event sequences for arbitrary charms in accordance with The Graph.
"""

# TODO:
# - event.defer()
# - ensure that pebble-ready random inserts don't come between
#   relation-joined -> relation-changed
# - [pre/post]-series-upgrade
# - early exit from operation phase based on... ?
# - unhappy path testing ?
# - also support actions during setup/teardown?


import logging
import random
import typing
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)
logger.setLevel('INFO')
logging.basicConfig(level='INFO')


@dataclass
class Event:
    name: str


class Source(Enum):
    user = 'user'
    this_charm = 'this_charm'
    other_charm = 'other_charm'

    @staticmethod
    def random() -> 'Source':
        return random.choice(list(Source))


class classproperty(property):
    def __get__(self, cls, owner):
        return staticmethod(self.fget).__get__(None, owner)()


@dataclass
class Action:
    subject: typing.Any
    name: str
    source: Source

    # generic actions that the user might perform at any time
    @classproperty
    def change_config():
        return Action(None, 'config-change', Source.user)

    @classproperty
    def scale_up():
        return Action(None, 'scale+', Source.user)

    @classproperty
    def scale_down():
        return Action(None, 'scale-', Source.user)

    @classproperty
    def leadership_change():
        """The act of changing leadership."""
        return Action(None, 'leadership_change', Source.random())

    def __hash__(self):
        return hash(((self.subject or 0), self.name, self.source))


@dataclass
class Relation:
    name: str
    is_peer: bool = False
    is_joined: bool = False

    @property
    def created(self) -> Event:
        return Event(self.name + '-relation-created')

    @property
    def broken(self) -> Event:
        return Event(self.name + '-relation-broken')

    @property
    def changed(self) -> Event:
        return Event(self.name + '-relation-changed')

    @property
    def joined(self) -> Event:
        return Event(self.name + '-relation-joined')

    @property
    def departed(self) -> Event:
        return Event(self.name + '-relation-departed')

    @property
    def change(self):
        """The act of changing a relation databag."""
        return Action(self, 'change', random.choice((Source.this_charm,
                                                     Source.other_charm)))

    @property
    def create(self):
        """The act of creating a novel relation."""
        return Action(self, 'create', Source.user)

    @property
    def destroy(self):
        """The act of breaking a relation."""
        return Action(self, 'break', Source.user)

    @property
    def join(self):
        """The act of joining a relation."""
        return Action(self, 'join', Source.user)

    @property
    def depart(self):
        """The act of departing from a relation."""
        return Action(self, 'depart', Source.user)

    def __hash__(self):
        return hash(self.name)


@dataclass
class StorageMount:
    name: str

    @property
    def detached(self) -> Event:
        return Event(self.name + '-storage-detached')

    @property
    def attached(self) -> Event:
        return Event(self.name + '-storage-attached')

    @property
    def attach(self):
        """The act of attaching some storage."""
        return Action(self, 'attach', Source.user)

    @property
    def detach(self):
        """The act of breaking a relation."""
        return Action(self, 'detach', Source.user)

    def __hash__(self):
        return hash(self.name)


@dataclass
class Container:
    name: str

    @property
    def pebble_ready(self) -> Event:
        return Event(self.name + '-pebble-ready')


class Phase(Enum):
    setup = 'setup'
    operation = 'operation'
    teardown = 'teardown'


class Platform(Enum):
    k8s = 'k8s'
    lxd = 'lxd'


# generic events that are not specific to a relation or a storage mount
pebble_ready = 'pebble-ready'
update_status = 'update-status'

start = Event('start')
stop = Event('stop')
install = Event('install')
remove = Event('remove')
config_changed = Event('config-changed')
leader_elected = Event('leader-elected')
leader_settings_changed = Event('leader-settings-changed')


def random_insert(lst: list, item: typing.Any):
    """Insert item at random index in list."""
    # fixme: this might come between relation-joined -> relation-changed which
    #   should not happen
    lst.insert(random.randrange(0, len(lst)), item)


class CharmEventSimulator:
    def __init__(self,
                 is_leader=True,
                 relations: typing.Sequence[Relation] = (),
                 containers: typing.Sequence[Container] = (Container('workload'), ),
                 storage_mounts: typing.Sequence[StorageMount] = (),
                 potential_relations: typing.Sequence[Relation] = (),
                 potential_storage_mounts: typing.Sequence[StorageMount] = (),
                 max_operation_length: int = 10,
                 scale: int = 1,
                 platform: Platform = Platform.k8s):
        """

        :param is_leader: whether this unit is leader;
            only has meaning if it has any relations
        :param relations:
            pre-existing relations that this charm joins as soon as it starts
        :param containers:
            containers in this charm
        :param storage_mounts:
            storage mounts this charm can use
        :param potential_relations:
            relations that this charm supports and could potentially
            join during its lifetime as a consequence of human operation
        :param potential_storage_mounts:
            storage that this charm supports and could potentially
            be added during its lifetime as a consequence of human operation
        :param max_operation_length:
            maximal duration of the operation phase
        :param scale:
            initial scale of this charm; how many units it consists of.
            Must be >=1
        :param platform:
            k8s | lxd
        """

        self.max_operation_length = max_operation_length
        self.is_leader = is_leader

        if not scale >= 1:
            raise ValueError('scale must be at least 1')

        self.scale = scale
        self.containers = containers = tuple(containers)

        # initial relations and storage mounts
        self.relations = relations = tuple(relations)
        self.storage_mounts = storage_mounts = tuple(storage_mounts)

        # relations and storage mounts that we aren't born with but might be
        #   added during my lifetime
        self.potential_relations = potential_relations = tuple(
            potential_relations)
        if any(potential_peers := filter(lambda relation: relation.is_peer,
                                         potential_relations)):
            raise ValueError(
                f"peer relations {potential_peers!r} cannot be potential; "
                f"they either are or aren't"
            )
        self.potential_storage_mounts = potential_storage_mounts = tuple(
            potential_storage_mounts)

        self.platform = platform

        self.has_pebble = platform is Platform.k8s
        # does <event> need to occur at least once in <phase>?
        self._event_must_occur = {
            Phase.setup: {
                pebble_ready: True,
                update_status: False
            },
            Phase.operation: {
                pebble_ready: False,
                update_status: False
            },
            Phase.teardown: {
                pebble_ready: False,
                update_status: False
            }}

        # chance that <event> will occur before/after
        #   any other event in <phase>
        #   update_status can be tweaked here to simulate 'how long'
        #   will a phase last.
        self._event_chances = {
            Phase.setup: {
                pebble_ready: .2,
                update_status: .1
            },
            Phase.operation: {
                pebble_ready: .05,
                update_status: .1
            },
            Phase.teardown: {
                pebble_ready: .01,
                update_status: .05
            }
        }

        # at any point in time...
        self.possible_actions = possible_actions = set(
            # ...any storage we have might be detached
            [storage.detach for storage in storage_mounts] +
            # ...the databag of a relation might be touched by another charm
            [relation.change for relation in relations] +
            # ...any relation we have might be removed
            [relation.destroy for relation in self._non_peer_relations] +
            # ...the user or another charm might change the config or scale +-
            [Action.change_config, Action.scale_up, Action.leadership_change] +
            # ... any potential relation can be actualized
            [relation.create for relation in potential_relations] +
            # ... any potential storage can be attached
            [storage.attach for storage in potential_storage_mounts]
        )

        if scale > 1:
            possible_actions.add(Action.scale_down)

        self.phase = None
        self.scenario = {
            Phase.setup: [],
            Phase.operation: [],
            Phase.teardown: [],
        }

    def clear(self):
        self.scenario = {
            Phase.setup: [],
            Phase.operation: [],
            Phase.teardown: [],
        }

    def add_relation(self, *relation: Relation):
        self.relations += relation

    def remove_relation(self, *relation: Relation):
        self.relations = tuple(r for r in self.relations if r not in relation)

    def attach_storage(self, *storage: StorageMount):
        self.storage_mounts += storage

    def detach_storage(self, *storage: StorageMount):
        self.storage_mounts = tuple(r for r in self.storage_mounts
                                    if r not in storage)

    @property
    def _peer_relations(self) -> typing.Iterable[Relation]:
        return filter(lambda relation: relation.is_peer, self.relations)

    @property
    def _non_peer_relations(self) -> typing.Iterable[Relation]:
        return filter(lambda relation: not relation.is_peer, self.relations)

    def run(self):
        logger.info('starting simulation...')
        with self._set_phase(Phase.setup):
            self._run_setup()
        with self._set_phase(Phase.operation):
            self._run_operation()
        with self._set_phase(Phase.teardown):
            self._run_teardown()
        logger.info('simulation done')

    @contextmanager
    def _set_phase(self, phase: Phase):
        old_phase = self.phase
        self.phase = phase

        yield

        self._close_phase(phase)
        self.phase = old_phase

    def _close_phase(self, phase: Phase):
        scenario = self.scenario[phase]
        # if pebble-ready has not occurred in phase,
        #   insert it manually somewhere.
        if self.has_pebble and self._event_must_occur[phase][pebble_ready]:
            for container in self.containers:
                if (c_pebble_ready := container.pebble_ready) not in scenario:
                    random_insert(scenario, c_pebble_ready)

        # same for update-status
        if self._event_must_occur[phase][update_status]:
            if update_status not in scenario:
                random_insert(scenario, Event(update_status))

    def _run_setup(self):
        # any storage available at install is attached
        for storage in self.storage_mounts:
            self._queue(storage.attached)

        self._queue(install)

        # any peer relation available at install is created
        if peer_relations := self._peer_relations:
            for relation in peer_relations:
                self._queue(relation.created)

            if self.is_leader:
                self._queue(leader_elected)
            else:
                self._queue(leader_settings_changed)

        self._queue(config_changed)
        self._queue(start)

    def _run_operation(self):
        n = 0
        max_operation_length = self.max_operation_length
        while n <= max_operation_length:
            possible_actions = self.possible_actions
            action = random.choice(tuple(possible_actions))
            self._exec(action)
            n += 1

    def _run_teardown(self):
        # break all relations
        for relation in self.relations:
            self._queue(relation.broken)
        # detach all storage
        for storage in self.storage_mounts:
            self._queue(storage.detached)
        self._queue(stop)
        self._queue(remove)

    def _queue(self,
               event: Event,
               allow: typing.Sequence[str] = None,
               disallow: typing.Sequence[str] = None):
        allow = (pebble_ready, update_status) if allow is None else allow
        disallow = disallow or ()

        sequence = [event]
        phase = self.phase
        scenario = self.scenario[phase]
        for random_event in allow:
            if random_event in disallow:
                continue

            chance = self._event_chances[phase][random_event]
            if chance and random.random() < chance:
                logger.info(
                    f'chance ({chance}) inserted {random_event} >> {phase}'
                )
                if random_event is pebble_ready and \
                        self.has_pebble and \
                        (containers := self.containers):
                    # determine for which container we're pebble-readying
                    container = random.choice(containers)
                    random_insert(sequence, container.pebble_ready)

                else:  # update-status: no preconditions
                    random_insert(sequence, Event(random_event))

        logger.info(f'queued {event}')
        scenario.extend(sequence)

    def _exec(self, action: Action, consume=True):
        logger.info(f'processing {action}')
        self.scenario[self.phase].append(action)

        subject = action.subject
        action_subj_type = type(subject)
        name = action.name

        if action_subj_type is Relation:
            subject: Relation
            if name == 'change':
                consume = False  # this can happen multiple times
                self._queue(subject.changed)

            elif name == 'create':
                self._queue(subject.created)
                self.add_relation(subject)
                # should probably fire 'joined' too at some point!

            elif name == 'joined':
                if subject not in self.relations:
                    raise RuntimeError(
                        f'attempting to join non-existing relation {subject}'
                    )
                if subject.is_joined:
                    raise RuntimeError(
                        f'attempting to re-join relation {subject}'
                    )
                # joined and IMMEDIATELY AFTER, changed.
                self._queue(subject.joined, allow=())
                self._queue(subject.changed, allow=())
                subject.is_joined = True

            elif name == 'break':
                self.remove_relation(subject)
                self._queue(subject.broken)

            elif name == 'depart':
                if subject not in self.relations:
                    raise RuntimeError(
                        f'attempting to depart non-existing relation {subject}'
                    )
                if not subject.is_joined:
                    raise RuntimeError(
                        f'attempting to depart non-joined relation {subject}'
                    )
                subject.is_joined = False

            else:
                raise ValueError(
                    f"{action_subj_type.__name__}-action "
                    f"{name!r} not recognized"
                )

        elif action_subj_type is StorageMount:
            subject: StorageMount

            if name == 'attach':
                if subject in self.storage_mounts:
                    raise RuntimeError(
                        f"attempting to re-attach storage {subject}"
                    )
                self.attach_storage(subject)
                self._queue(subject.attached)

            elif name == 'detach':
                if subject not in self.storage_mounts:
                    raise RuntimeError(
                        f"attempting to detach unknown storage {subject}"
                    )
                self.detach_storage(subject)
                self._queue(subject.detached)

            else:
                raise ValueError(
                    f"{action_subj_type.__name__}-action "
                    f"{name!r} not recognized"
                )

        elif subject is None:
            # generic actions;
            consume = False

            if name == 'config-change':
                self._queue(config_changed)

            if name == 'scale+':
                # fire a relation-joined for all relations
                for relation in self.relations:
                    if not relation.is_joined:
                        logger.info(
                            f'{relation} not joined: scale+ will not '
                            f'trigger a relation-joined'
                        )
                        continue

                    self.scale += 1
                    # we can definitely scale down now
                    self.possible_actions.add(Action.scale_down)

                    # we don't recurse on _exec because it's messy
                    self._queue(relation.joined, allow=())
                    self._queue(relation.changed, allow=())
                else:
                    logger.info('no relations: scale+ will trigger no events')

            if name == 'scale-':
                for relation in self.relations:
                    if not relation.is_joined:
                        logger.info(
                            f'{relation} not joined: scale- will not '
                            f'trigger a relation-joined'
                        )
                        continue

                    if self.scale == 1:
                        # can't scale down any further
                        consume = True

                    self._queue(relation.departed)
                else:
                    logger.info('no relations: scale- will trigger no events')

            if name == 'leadership_change':
                if self.is_leader:
                    # stop being leader:
                    self.is_leader = False
                    self._queue(leader_settings_changed)
                else:  # become one
                    self.is_leader = True
                    self._queue(leader_elected)

        else:
            raise ValueError(
                f'no action known for type {action_subj_type!r}'
            )

        if consume:
            self.possible_actions.remove(action)

        logger.info(f'processed {action}')

    def pprint(self):
        print('simulation:')
        for phase, events in self.scenario.items():
            print(f'PHASE {phase}:')
            for event in events:
                if isinstance(event, Event):
                    print(f'    Event  :: {event.name}')
                else:  # Action
                    event: Action
                    subj = getattr(event.subject, "name", "")
                    print(f'    Action :: {event.source} --> {event.name!r}'
                          f'{"(%s)"%subj if subj else ""}')
            print()
        print('end.')


if __name__ == '__main__':
    sim = CharmEventSimulator(
        storage_mounts=[
            StorageMount('storage1')],
        relations=[
            Relation('http'),
            Relation('replicas', is_peer=True)
        ],
        potential_relations=[
            Relation('mongo')
        ],
        potential_storage_mounts=[
            StorageMount('ephemeral1')
        ]
    )
    sim.run()
    sim.pprint()
