This is a temporary repository to work on the charm events graph while we find a place for it in the existing documentation or a juju repo.
It also includes some made-up notes to understanding the graph.

# The Graph
```mermaid
flowchart TD
    subgraph Setup
        storage_attached["[*]-storage-attached"]:::optStorageEvent --> install
        install --> relation_created["[*]-relation-created"]:::optRelationEvent
        relation_created --> |leader unit|leader_elected[leader-elected]:::optLeaderEvent
        relation_created --> |non-leader unit|leader_settings_changed[leader-settings-changed]:::optLeaderEvent
        leader_settings_changed --> config_changed[config-changed]
        leader_elected --> config_changed[config-changed]
        config_changed --> start
    end

    subgraph Maintenance
        upgrade_charm[upgrade-charm] --- 
        update_status[update-status] ---
        config_changed_mant[config-changed] 
        leader_elected_mant[leader-elected]:::leaderEvent --- 
        leader_settings_changed_mant[leader-settings-changed]:::leaderEvent
        relation_joined_mant[<*>-relation-joined]:::relationEvent -.- relation_departed_mant[<*>-relation-departed]:::relationEvent
        relation_joined_mant --> relation_changed_mant[<*>-relation-changed]:::relationEvent 
        relation_created_mant[<*>-relation-created]:::relationEvent -.- relation_broken_mant[<*>-relation-broken]:::relationEvent 
        storage_attached_mant[<*>-storage-attached]:::storageEvent -.- storage_detached_mant[<*>-storage-detached]:::storageEvent
    end
    
    subgraph Teardown
        relation_broken_teard[<*>-relation-broken]:::optRelationEvent -->
        storage_detached[<*>-storage-detached]:::optStorageEvent -->
        stop -->
        remove
    end
    
    Start:::meta --> 
    Setup --> 
    Maintenance --> 
    Teardown --> 
    End:::meta

linkStyle 7 stroke:#fff,stroke-width:1px;
linkStyle 8 stroke:#fff,stroke-width:1px;
linkStyle 9 stroke:#fff,stroke-width:1px;

classDef relationEvent fill:#f9f5;
classDef storageEvent fill:#f995;
classDef leaderEvent fill:#5f55;
classDef meta fill:#1112,stroke-width:3px;

classDef optRelationEvent fill:#f9f5,stroke-dasharray: 5 5;
classDef optStorageEvent fill:#f995,stroke-dasharray: 5 5;
classDef optLeaderEvent fill:#5f55,stroke-dasharray: 5 5;
```

## Understanding the graph
You can read the graph as follows: when you fire up a unit, there is first a setup phase, when that is done the unit enters a maintenance phase, and when the unit goes there will be a sequence of teardown events. Generally speaking, this guarantees some sort of ordering of the events: events that are unique to the teardown phase can be guaranteed not to be fired during the setup phase. So a `stop` will never be fired before a `start`.

The obvious omission from the graph is the `<*>-pebble-ready` event, which can be fired at any time whatsoever during the lifecycle of a charm; similarly all actions and custom events can trigger hooks which can race with any other hook in the graph. Lacking a way to add them to the mermaid graph without ruining its simmetry and so as to avoid giving the wrong impression, I omitted these altogether. 

`[pre/post]-series-upgrade` machine charm events are also omitted, but these are simply part of the maintenance phase. Summary below:

```mermaid
flowchart TD
    subgraph Anytime[Any Time]
        action["[*]-action"]:::custom
        customevent["[custom event name]"]:::custom
        
        subgraph Kubernetes[Only on Kubernetes]
            pebble_ready["[*]-pebble-ready"]:::kubevent
        end
    end
    
    subgraph Maintenance [Maintenance -- Machine]
        pre_series_upgrade[pre-series-upgrade]:::machine -.- post_series_upgrade[post-series-upgrade]:::machine
    end
    
classDef kubevent fill:#77f5;
classDef custom fill:#9995;
classDef machine fill:#2965;
```

### Notes on the Setup phase
* The only events that are guaranteed to always occur during Setup are `start` and `install`. The other events only happen if the charm happens to have (peer) relations at install time (e.g. if a charm that already is related to another gets scaled up) or it has storage. Same goes for leadership events. For that reason they are styled with dashed borders.
* `config-changed` occurs between `start` and `install` regardless of whether any leadership (or relation) event fires.

### Notes on the Maintenance phase
* `update-status` is fired automatically and periodically, at a configurable interval (default is 5m).
* `leader-elected` and `leader-settings-changed` only fire on the leader unit and the non-leader unit(s) respectively, just like at startup.
* There is a square of symmetries between the `*-relation-[joined/departed/created/broken]` events:
  * Temporal ordering: a `X-relation-joined` cannot *follow* a `X-relation-departed` for the same X. Same goes for `*-relation-created` and `*-relation-broken`, as well as `*-relation-created` and `*-relation-changed`.
  * Ownership: `*-relation-joined` and `*-relation-created` only fire on unit(s) that is(are) already in the relation and the unit(s) that is(are) joining respectively. Same goes for `*-relation-departed` and `*-relation-broken`
* Technically speaking all events in this box are optional, but I did not style them with dashed borders to avoid clutter. If the charm shuts down immediately after start, it could happen that no maintenance event is fired.
* A `X-relation-joined` event is always followed up (immediately after) by a `X-relation-changed` event.


### Notes on the Teardown phase
* Both relation and storage events are guaranteed to fire before `stop/remove` if the charm has storage/relations. Otherwise, only stop/remove will be fired.

# Event semantics and data
This document is only about the timing of the events; for the 'meaning' of the events, other sources are more appropriate; e.g. [juju-events](https://juju.is/docs/sdk/events).
For the data attached to an event, one should refer to the docstrings in the ops.charm.HookEvent subclass that the event you're expecting in your handler inherits from.
