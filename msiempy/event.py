"""Provide event management. Define `EventManager`, `Event`, `FieldFilter`, `GroupFilter`, `GroupedEventManager`, `GroupedEvent`.  

Base object: 
    - `_QueryExecuteManager`
"""

import time
import collections
import logging
from datetime import datetime, timedelta

log = logging.getLogger("msiempy")

from .core import NitroDict, NitroError, FilteredQueryList
from .core.utils import (
    timerange_gettimes,
    parse_query_result,
    format_fields_for_query,
    divide_times,
    parse_timedelta,
)
from .device import DevTree


class _QueryExecuteManager(FilteredQueryList):
    """
    *Abstract* class to handle common `filters` properties that grouped and non-grouped queries share.

    Also provide helper functions to wait the query and get the results (see source code).
    Only Events query are currently supported.
    """

    _TYPE = "EVENT"
    """`EVENT`: Flow queries are not implemented (yet)"""

    def __init__(self, *args, **kwargs):

        # Declaring filter attributes before calling super() because it would overwrite values
        self._filters = []

        # Calling super constructor : time_range, filters etc...
        super().__init__(*args, **kwargs)

    def _get_filters(self):
        """
        Returns SIEM formatted filters for the query structured from `msiempy.event.GroupFilter` and/or `msiempy.event.FieldFilter`
        See `msiempy.core.query.FilteredQueryList.filters`.
        """
        return [dict(f) for f in self._filters]

    def add_filter(self, afilter):
        """
        Add a filter to the query.  
        
        Called by the `filters` property setter.  

        Arguments:
            - `afilter` (`tuple(field, [values])` or `tuple(field, value)` or `msiempy.event.GroupFilter` or `msiempy.event.FieldFilter`): The filter
        """
        if isinstance(afilter, tuple):
            self._filters.append(FieldFilter(afilter[0], afilter[1]))

        elif isinstance(afilter, (GroupFilter, FieldFilter, dict)):
            self._filters.append(afilter)

        else:
            raise TypeError(
                "Sorry, filters must be either a tuple a GroupFilter, a FieldFilter or a dict. Not {}".format(
                    afilter
                )
            )

    def _close_query(self, resultID):
        """
        Close the query

        Internal method called by _qry_load_data
        """
        self.nitro.request("close_query", resultID=resultID)

    def _wait_for(self, resultID, wait_timeout_sec, sleep_time=0.2):
        """
        Wait and sleep for the query.  

        Internal method called by _qry_load_data
        
        Arguments:
            - `resultID`: Query result ID
            - `wait_timeout_sec` (`int`): Duration in seconds until the query is completed or countdown arrives at zero.
            - `sleep_time` (`float`): Time to sleep in the waiting loop

        Returns: 
            `True`

        Raises:
            - `msiempy.NitroError`: 'ResultUnavailable' error some times...
            - `TimeoutError`: Query wait timeout
        """

        begin = datetime.now()
        timeout_delta = timedelta(seconds=wait_timeout_sec)

        log.debug("Waiting for the query to be executed on the SIEM...")

        while datetime.now() - timeout_delta < begin:
            status = self.nitro.request(
                "query_status", resultID=resultID  # ['value'] # APIv2 change
            )
            if status["complete"] is True:
                return True
            else:
                time.sleep(sleep_time)
        raise TimeoutError(
            "Query wait timeout. resultID={}, sleep_time={}, wait_timeout_sec={}".format(
                resultID, sleep_time, wait_timeout_sec
            )
        )

    def _get_events(self, resultID, startPos=0, numRows=500):
        """
        Internal method that will get the query events. 
        Called by `_qry_load_data`.
        By default, ``numRows`` correspond to ``limit``.  
        """
        result = self.nitro.request(
            "query_result",
            startPos=startPos,
            numRows=numRows,
            resultID=resultID,  # ['value'] # APIv2 change
        )

        # Calls a utils function to parse the [columns][rows]
        #   to format into list of dict
        # log.debug("Parsing colums : "+str(result['columns'])[:200])
        # log.debug("Parsing rows : "+str(result['rows'])[:200])
        if len(result["columns"]) != len(
            set([column["name"] for column in result["columns"]])
        ):
            log.error(
                "You requested duplicated fields, the parsed fields/values results will be missmatched !"
            )
        events = parse_query_result(result["columns"], result["rows"])
        # log.debug("Event(s) parsed : "+str(events)[:200])
        return events

    @staticmethod
    def get_field_nickname(field):
        """
        Resolve SIEM events field nickname base on `Event.SIEM_FIELDS_MAP_INTERNAL_NAME_TO_NICKNAME` mapping.
        Returns the valid query field nickname if found else the initial value.
        """
        if field in Event.SIEM_FIELDS_MAP_INTERNAL_NAME_TO_NICKNAME:
            return Event.SIEM_FIELDS_MAP_INTERNAL_NAME_TO_NICKNAME[field]
        else:
            return field


class EventManager(_QueryExecuteManager):
    """
    List-Like object. Interface to execute a event query.

    Exemples:
        - Execute an event query 

        Query events according to destination IP and hostname filters, sorted by AlertID.  

        .. python::

                from  msiempy import EventManager, FieldFilter
                print('Simple event query sorted by AlertID')
                events = EventManager(
                        time_range='CURRENT_YEAR',
                        fields=['SrcIP', 'AlertID'], # SrcIP and AlertID are not queried by default
                        filters=[
                                FieldFilter('DstIP', ['0.0.0.0/0',]),
                                FieldFilter('HostID', ['mail'], operator='CONTAINS')], # Replace "mail" by a test hostname
                        order=(('ASCENDING', 'AlertID')),
                        limit=10) # Should be increased to 500 or 1000 once finish testing for better performance
                events.load_data()
                print(events)
                print(events.get_text(fields=['AlertID','LastTime','SrcIP', 'Rule.msg']))

        Note: 
            You can dump full list of fields usable in query `FieldFilter` with `dump_all_fields.py <https://github.com/mfesiem/msiempy/blob/master/samples/dump_all_fields.py>`_ script.  

        - Add a note to events

        Set the note of some events and check if the note is well set.  

        .. python::

                from  msiempy import EventManager, Event
                events = EventManager(
                        time_range='CURRENT_YEAR',
                        limit=2 )
                events.load_data()
                for event in events :
                        event.set_note("Test note")
                        event.refresh(use_query=False) # Event data will be loaded with ipsGetAlertData API method
                        assert "Test note" in genuine_event['note'], "Error, the note hasn't been added"

        See: 
                - `add_wpsan_note.py <https://github.com/mfesiem/msiempy/blob/master/samples/add_wpsan_note.py>`_ script for more on how to add notes to event that triggered alarms.       

    See: 
            Objects `Event` and `FieldFilter`
            
    """

    # Constants
    _GROUPTYPE = "NO_GROUP"
    """`NO_GROUP`: EventManager handles only events see `GroupedEventManager` for grouped queries"""
    POSSBILE_ROW_ORDER = ["ASCENDING", "DESCENDING"]
    """``"ASCENDING"`` or ``"DESCENDING"``"""

    def __init__(
        self, *args, fields=None, order=None, limit=500, _parent=None, **kwargs
    ):
        """
        Create a new event query.  

        Arguments: 
            - `fields` (`list[str]`): Query fields
            - `order` (`tuple(direction, field)`): Query order direction and field. Direction can be ``"ASCENDING"`` or ``"DESCENDING"``. 
            - `limit` (int): Max number of rows per query result.
            - `filters` (list[`tuple(field, [values])` or `FieldFilter` or `GroupFilter`]): Query filters
            - `time_range` (`str`): Query time range. No need to specify ``"CUSTOM"`` if ``start_time`` and ``end_time`` are set.
            - `start_time` (`str` or `datetime`): Query start time
            - `end_time` (`str` or `datetime`): Query end time

        Note: 
            Some minimal fields will always be present. Get the list of possible fields with `EventManager.get_possible_fields`

        See: 
            `Event`

        """
        # Calling super constructor : filters, time_range set etc...
        super().__init__(*args, **kwargs)

        # Store the query parent
        self._parent = _parent

        # Setting the default fields Adds the specified fields, make sure there is no duplicates and delete TABLE identifiers
        self.fields = []
        """
        List of query fields
        """

        if fields and len(fields) > 0:
            all_keys = Event.DEFAULTS_EVENT_FIELDS + list(fields)
            uniquekeys = set()
            for k in all_keys:
                uniquekeys.add(self.get_field_nickname(k))
            self.fields = list(uniquekeys)
        else:
            self.fields = Event.DEFAULTS_EVENT_FIELDS
        # log.debug('{}\nFIELDS : {}'.format(locals(), self.fields))

        # Setting limit according to limit argument
        # TODO Try to load queries with a limit of 10k and get result as chucks of 500 with starPost nbRows
        #   and compare efficiency
        self.limit = int(limit)
        """
        Maximum number of rows per query.  
        """

        # Save order
        self.order = order

        # Type cast all items in the list "data" to events type objects
        # Casting all data to Event objects, better way to do it ?
        collections.UserList.__init__(
            self,
            [
                Event(adict=item)
                for item in self.data
                if isinstance(item, (dict, NitroDict))
            ],
        )

    
    def _get_order(self):
        return (self._order_direction, self._order_field)

    def _set_order(self, order):
        if order:
            try:
                if order[0] not in self.POSSBILE_ROW_ORDER:
                    raise AttributeError(
                        "Order direction must be in " + str(self.POSSBILE_ROW_ORDER)
                    )

                self._order_direction = order[0]
                self._order_field = order[1]
            except IndexError:
                raise ValueError("Order must be tuple (direction, field).")
        else:
            self._order_direction = "DESCENDING"
            self._order_field = "LastTime"

    order = property(fget=_get_order, fset=_set_order)
    """
    The `order` is a `tuple (direction, field)`.
    Default value is ``("DESCENDING", "LastTime")``.
    """

    def clear_filters(self):
        """
        Replace all filters by a non filtering rule.
        Acts like there is not filters.
        """
        self._filters = [
            {
                "type": "EsmFieldFilter",
                "field": {"name": "SrcIP"},
                "operator": "IN",
                "values": [{"type": "EsmBasicValue", "value": "0.0.0.0/0"}],
            }
        ]

    def _qry_load_data(self, retry=1, wait_timeout_sec=120):
        """
        Internal helper method to execute the query and load the data:
            - Submit the query
            - Wait the query to be executed
            - Get and parse the events

        Arguments:
            - `retry` (`int`): number of time the query can be failed and retried.  (Default value = 1)
            - `wait_timeout_sec` (`int`): wait timeout in seconds. (Default value = 120)

        Returns: 
            tuple: ( `msiempy.event.EventManager`, Query completed? `bool` )

        Raises:
            - `msiempy.core.session.NitroError`: If any unhandled errors.
            - `TimeoutError`: If ``wait_timeout_sec`` counter gets to 0.
        """
        try:
            query_infos = dict()

            # Queries api calls are very different if the time range is custom.
            if self.time_range == "CUSTOM":
                query_infos = self.nitro.request(
                    "event_query_custom_time",
                    time_range=self.time_range,
                    start_time=self.start_time,
                    end_time=self.end_time,
                    order_direction=self._order_direction,
                    order_field=self._order_field,
                    fields=format_fields_for_query(self.fields),
                    filters=self.filters,
                    limit=self.limit,
                    offset=0,
                    includeTotal=False,
                )

            else:
                query_infos = self.nitro.request(
                    "event_query",
                    time_range=self.time_range,
                    order_direction=self._order_direction,
                    order_field=self._order_field,
                    fields=format_fields_for_query(self.fields),
                    filters=self.filters,
                    limit=self.limit,
                    offset=0,
                    includeTotal=False,
                )

            log.debug("Waiting for EsmRunningQuery object : " + str(query_infos))

            self._wait_for(query_infos["resultID"], wait_timeout_sec)
            events_raw = self._get_events(query_infos["resultID"], numRows=self.limit)
            self._close_query(query_infos["resultID"])

        except (NitroError, TimeoutError) as error:
            if retry > 0:
                log.warning("Retring _qry_load_data() after error: " + str(error))
                time.sleep(1)
                return self._qry_load_data(retry=retry - 1)
            else:
                raise

        return (events_raw, len(events_raw) < self.limit)

    def load_data(self, workers=10, slots=10, delta=None, max_query_depth=0, **kwargs):
        """
        **Load the events data into the list.**  
        Wraps around `msiempy.event.EventManager._qry_load_data`.

        Arguments:
            - `max_query_depth` (`int`): Maximum number of reccursive divisions `load_data` method can apply to the query in order to load all events. Splits the query in differents time slots if the query apprears not to be completed.  Only works with custom times and some time ranges.
                If ``EventManager.limit=500``, ``slots=10`` and ``max_query_depth=2``, then the maximum capacity of the list is ``(500*10)*(500*10)`` = ``25000000`` (instead of ``500`` with ``max_query_depth=0``). 
            - `slots` (`int`): number of time slots the query can be divided. Loading bar is divided according to the number of slots. Applicable if ``max_query_depth>0``.
            - `delta` (`str`): exemple : '2h', the query will be firstly divided in chuncks according to the time delta read with `dateutil`. Applicable if ``max_query_depth>0``. 
            - `workers` (`int`): numbre of parrallels tasks, should be equal or less than the number of slots. Applicable if ``max_query_depth>0``. 
            - `retry` (`int`): number of time the query can be failed and retried.  (Default value = 1)
            - `wait_timeout_sec` (`int`): wait timeout in seconds. (Default value = 120)

        Returns: 
            `msiempy.event.EventManager`

        Note: 
            Only the first query is loaded asynchronously.
        """

        items, completed = self._qry_load_data()

        if not completed:
            # If not completed the query is split and items aren't actually used

            if max_query_depth > 0:
                # log.info("The query data couldn't be loaded in one request, separating it in sub-queries...")

                if (
                    self.time_range != "CUSTOM"
                ):  # can raise a NotImplementedError if unsupported time_range
                    start, end = timerange_gettimes(self.time_range)
                else:
                    start, end = self.start_time, self.end_time

                if self._parent == None and isinstance(delta, str):
                    # if it's the first query and delta is speficied, cut the time_range in slots according to the delta
                    times = divide_times(start, end, delta=parse_timedelta(delta))

                else:
                    times = divide_times(start, end, slots=slots)

                if workers > len(times):
                    log.warning(
                        "The number of slots is smaller than the number of workers, only "
                        + str(len(times))
                        + " asynch workers will be used when you could use up to "
                        + str(workers)
                        + ". Number of slots should be greater than the number of workers for better performance."
                    )

                sub_queries = list()

                for time in times:  # reversed(times) :
                    # Divide the query in sub queries
                    sub_query = EventManager(
                        fields=self.fields,
                        order=self.order,
                        limit=self.limit,
                        filters=self._filters,
                        time_range="CUSTOM",
                        start_time=time[0].isoformat(),
                        end_time=time[1].isoformat(),
                        _parent=self,
                    )

                    sub_queries.append(sub_query)

                results = self.perform(
                    EventManager.load_data,
                    sub_queries,
                    # The sub query is asynch only when it's the first query (root parent)
                    asynch=self._parent == None,
                    progress=self._parent == None,
                    message="Loading data from "
                    + start
                    + " to "
                    + end
                    + ". In {} slots".format(len(times)),
                    func_args=dict(slots=slots, max_query_depth=max_query_depth - 1),
                    workers=workers,
                )

                # Flatten the list of lists in a list
                items = [item for sublist in results for item in sublist]

            else:
                if not self._root_parent.not_completed:
                    log.warning(
                        "The query is not complete... Try to divide in more slots or increase max_query_depth"
                    )
                    self._root_parent.not_completed = True

        events = [Event(adict=item) for item in items]
        self.data = events
        return self

    @property
    def _root_parent(self):
        """
        Internal method that return the first query of the query tree.
        """
        if self._parent == None:
            return self
        else:
            return self._parent._root_parent

    def get_possible_fields(self):
        """
        Return the list of possible fields that you can request in a Events query.
        The list is loaded from the SIEM.
        """
        return self.nitro.request(
            "get_possible_fields", type=self._TYPE, groupType=self._GROUPTYPE
        )

    def get_possible_filters(self):
        """
        Return the list of possible fields that you can use as a filter in a query.
        The list is loaded from the SIEM.
        """
        return self.nitro.request("get_possible_filters")


class GroupedEventManager(_QueryExecuteManager):
    """
    List-Like object. Interface to execute a grouped event query.

    Exemples:
        - Execute a grouped event query:

        Query the curent day events filtered by `IPSID` grouped by `ScrIP`.  

        .. python::

                from msiempy import GroupedEventManager
                import pprint
                query = GroupedEventManager(
                                time_range='LAST_3_DAYS', 
                                field='SrcIP', 
                                filters=[('IPSID', '144116287587483648')]) 
                query.load_data()
                # Sort the results by total count
                results = list(reversed(sorted(query, key=lambda k: int(k['SUM(Alert.EventCount)']))))
                # Display top 10
                top10=results[:10]
                pprint.pprint(top10)


    See:
            Object `GroupedEvent`.  

    Tip:
            `all_dev.py script <https://github.com/mfesiem/msiempy/blob/master/samples/all_dev.py>`_ can help you list all your datasources IDs (for the required ``IPSID`` filter).  


    """

    def __init__(self, *args, field=None, **kwargs):
        """
        Create a new grouped query

        Arguments:
            - `field` (`str`): The field that will be selected when this query is executed.
            - `filters` (`list`): list of filters. A filter can be a `tuple(field, [values])` or it can be a `msiempy.event.FieldFilter` or `msiempy.event.GroupFilter` if you wish to use advanced filtering.
            - `time_range` (`str`): Query time range. String representation of a time range. Not need to specify ``"CUSTOM"`` if `start_time` and `end_time` are set.
            - `start_time` (`str` or a `datetime`): Query start time.
            - `end_time` (`str` or a `datetime`): Query end time.
        """
        # Calling super constructor : time_range set etc...
        super().__init__(*args, **kwargs)

        # Declaring attributes
        self.field = None
        """
        Grouped query field
        """
        if field:
            if not isinstance(field, str):
                raise TypeError("Argument field must be a string. Not {}".format(field))
            self.field = self.get_field_nickname(field)

        # Type cast all items in the list "data" to events type objects
        # Casting all data to Event objects, better way to do it ?
        collections.UserList.__init__(
            self,
            [
                GroupedEvent(item)
                for item in self.data
                if isinstance(item, (dict, NitroDict))
            ],
        )

    def load_data(self, *args, **kwargs):
        """
        Load the data into the list.

        Arguments:
            - `num_rows` (`int`): Maximum number of rows to load.
            - `retry` (`int`): number of time the query can be failed and retried.
            - `wait_timeout_sec` (`int`): wait timeout in seconds.

        Returns: 
            `GroupedEventManager`
        """
        items, completed = self._qry_load_data(*args, **kwargs)
        if not completed:
            log.warning("The query is not complete... Try to increase the num_rows")
        self.data = [GroupedEvent(item) for item in items]
        return self

    def clear_filters(self):
        """
        Replace all filters by a non filtering rule with all datasources IPSIDs (Using `msiempy.device.DevTree`).  
        Acts like there is no filters.
        """
        log.info(
            "Setting a generic filter to the grouped query with all datasources IPSIDs..."
        )
        tree = DevTree()
        dsids = [d["ds_id"] for d in tree]
        self._filters = [
            {
                "type": "EsmFieldFilter",
                "field": {"name": "IPSID"},
                "operator": "IN",
                "values": [{"type": "EsmCompoundValue", "values": dsids}],
            }
        ]

    def _qry_load_data(self, num_rows=500, retry=1, wait_timeout_sec=120):
        """
        Helper method to execute the grouped query and load the data:
            - Submit the query
            - Wait the query to be executed
            - Get and parse the events

        Arguments:
            - `num_rows` (`int`): Maximum number of rows to load.
            - `retry` (`int`): number of time the query can be failed and retried.
            - `wait_timeout_sec` (`int`): wait timeout in seconds.

        Returns:
            tuple : ( `list`, Query completed? `bool` )

        Raises:
            - `msiempy.core.session.NitroError` if any unhandled errors.
            - `TimeoutError` if ``wait_timeout_sec`` counter gets to 0.
            - `ValueError` if an ``IPSID`` filter is not present.
        """
        if not any([f["field"]["name"] == "IPSID" for f in self.filters]):
            raise ValueError(
                "An 'IPSID' filter must be specified when issuing a grouped query.  "
            )
        try:
            query_infos = dict()

            # Queries api calls are very different if the time range is custom.
            if self.time_range == "CUSTOM":
                query_infos = self.nitro.request(
                    "grouped_event_query_custom_time",
                    time_range=self.time_range,
                    start_time=self.start_time,
                    end_time=self.end_time,
                    field=self.field,
                    filters=self.filters,
                )

            else:
                query_infos = self.nitro.request(
                    "grouped_event_query",
                    time_range=self.time_range,
                    field=self.field,
                    filters=self.filters,
                )

            log.debug("Waiting for EsmRunningQuery object : " + str(query_infos))

            self._wait_for(query_infos["resultID"], wait_timeout_sec)
            events_raw = self._get_events(query_infos["resultID"], numRows=num_rows)
            self._close_query(query_infos["resultID"])

        except (NitroError, TimeoutError) as error:
            if retry > 0:
                log.warning("Retring _qry_load_data() after error: " + str(error))
                time.sleep(1)
                return self._qry_load_data(retry=retry - 1)
            else:
                raise

        return (events_raw, len(events_raw) < num_rows)


class Event(NitroDict):
    """
    Dict-Like object. Represents an event in the SIEM.  

    This object handles 3 types of events objects:  
        - created from the ``qryGetResults`` API method: 
            Using `EventManager` or `Event`.  

            **Common keys**:
            ``Rule.msg``,
            ``Alert.LastTime``, 
            ``Alert.IPSIDAlertID``
            **And any other** :
            dump the available fields and filters with `dump_all_fields.py <https://github.com/mfesiem/msiempy/blob/master/samples/dump_all_fields.py>`_ script.  


        - created from the ``ipsGetAlertData`` API method: 
            Using `AlarmManager` or `Event` 

            **Common keys** for ``ipsGetAlertData`` events:
            ``ruleName``,
            ``srcIp``,
            ``destIp``,
            ``protocol``,
            ``lastTime``,
            ``subtype``,
            ``destPort``,
            ``destMac``,
            ``srcMac``,
            ``srcPort``,
            ``deviceName``,
            ``sigId``,
            ``normId``,
            ``srcUser``,
            ``destUser``,
            ``normMessage``,
            ``normDesc``,
            ``host``,
            ``domain``,
            ``ipsId``
            **And others**

        - created from the ``notifyGetTriggeredNotificationDetail`` API method (SIEM v11.x only): 
            Using `AlarmManager`  if ``events_details=False`` is passed to `AlarmManager.load_data` method

            **All keys**:
                ``ruleMessage``,
                ``eventId``,
                ``severity``,
                ``eventCount``,
                ``sourceIp``,
                ``destIp``,
                ``protocol``,
                ``lastTime`` and 
                ``eventSubType``
        
    For ``qryGetResults`` events: 
        We tried our best effort to match SIEM returned fields with initially requested fields.  
        `__getitem__` and `__contains__`, method have been rewrote in order to offer more straight-forward `dict` usage.  
        
        Exemple:

        >>> e = EventManager(fields=["Web_Doamin","UserIDSrc","SrcIP"]).load_data()[0]

        Then, the following expressions are equivalent:

        ======================   ===============   ======================
        >>> e["Alert.65613"]     is the same as    >>> e["Web_Doamin"]  
        >>> e["Alert.BIN(7)"]    is the same as    >>> e["UserIDSrc"]
        >>> e["Alert.SrcIP"]     is the same as    >>> e["SrcIP"]
        >>> "Alert.SrcIP" in e   is the same as    >>> "SrcIP" in e
        ======================   ===============   ======================

    """

    FIELDS_TABLES = [
        "Alert",
        "Rule",
        "ADGroup",
        "Action",
        "Asset",
        "AssetGroup",
        "AssetThreat",
        "CaseMgt",
        "CaseOrg",
        "CaseStatus",
        "Class",
        "Connection",
        "DataEnrichment",
        "GeoLoc_ASNGeoDst",
        "GeoLoc_ASNGeoSrc",
        "IOC",
        "IPS",
        "IPSCheck",
        "NDDeviceInterface_NDDevIFDst",
        "NDDeviceInterface_NDDevIFSrc",
        "NDDevice_NDDevIDDst",
        "NDDevice_NDDevIDSrc",
        "OS",
        "Rule_NDSNormSigID",
        "Tag",
        "TagAsset",
        "ThirdPartyType",
        "Threat",
        "ThreatVendor",
        "TriggeredAlarm",
        "Users",
        "Vulnerability",
        "Zone_ZoneDst",
        "Zone_ZoneSrc",
    ]
    """List of internal fields table : `Rule`,`Alert`,etc.
    """

    # Minimal default query fields
    DEFAULTS_EVENT_FIELDS = ["Rule.msg", "LastTime", "IPSIDAlertID"]
    """Always present when using `msiempy.event.EventManager` querying :  
        `Rule.msg`  
        `Alert.LastTime`  
        `Alert.IPSIDAlertID`
    """
    # Regular query fields
    REGULAR_EVENT_FIELDS = [
        "Rule.msg",
        "Alert.SrcIP",
        "Alert.DstIP",
        "Alert.SrcMac",
        "Alert.DstMac",
        "Rule.NormID",
        "HostID",
        "UserIDSrc",
        "ObjectID",
        "Alert.Severity",
        "Alert.LastTime",
        "Alert.DSIDSigID",
        "Alert.IPSIDAlertID",
    ]
    """
    Offer a base list of regular fields that may be useful.

    ``Rule.msg``,  ``Alert.SrcIP``,  ``Alert.DstIP``,   ``Alert.SrcMac``,  ``Alert.DstMac``,  ``Rule.NormID``,  ``HostID``,  ``UserIDSrc``,  ``ObjectID``,  ``Alert.Severity``,  ``Alert.LastTime``,  ``Alert.DSIDSigID``,  ``Alert.IPSIDAlertID`` 
    """

    SIEM_FIELDS_MAP_INTERNAL_NAME_TO_NICKNAME = {
        "Alert.105250817": "DNS - Response_Code_Name",
        "Alert.122028033": "DNS - Query",
        "Alert.196609": "Queue_ID",
        "Alert.21364737": "DNS - Class",
        "Alert.21364738": "Registry - Key",
        "Alert.21364739": "Old_Reputation - GTI_File",
        "Alert.21364740": "New_Reputation - GTI_File",
        "Alert.262145": "Response_Time",
        "Alert.262146": "NAT_Details",
        "Alert.262152": "PID",
        "Alert.262153": "Grid_Master_IP",
        "Alert.262154": "Device_IP",
        "Alert.262155": "Device_Port",
        "Alert.262156": "External_EventID",
        "Alert.262157": "Spam_Score",
        "Alert.262158": "External_SubEventID",
        "Alert.262159": "File_Hash",
        "Alert.262160": "Handle_ID",
        "Alert.262161": "Instance_GUID",
        "Alert.262162": "Agent_GUID",
        "Alert.262163": "UUID",
        "Alert.262164": "Reputation",
        "Alert.262165": "DAT_Version",
        "Alert.262166": "Server_ID",
        "Alert.262167": "Policy_ID",
        "Alert.262168": "Handheld_ID",
        "Alert.262169": "Database_GUID",
        "Alert.262170": "Analyzer_DAT_Version",
        "Alert.262171": "Reputation_Score",
        "Alert.262172": "Parent_File_Hash",
        "Alert.262173": "Incident_ID",
        "Alert.262174": "Victim_IP",
        "Alert.262175": "Attacker_IP",
        "Alert.262176": "Object_GUID",
        "Alert.262177": "Reputation_Server_IP",
        "Alert.262178": "DNS_Server_IP",
        "Alert.262179": "Device_Confidence",
        "Alert.38141953": "DNS - Class_Name",
        "Alert.38141954": "Registry - Value",
        "Alert.38141955": "Old_Reputation - TIE_File",
        "Alert.38141956": "New_Reputation - TIE_File",
        "Alert.4259841": "URL",
        "Alert.4259842": "Message_Text",
        "Alert.4259843": "Filename",
        "Alert.4259844": "From",
        "Alert.4259845": "To",
        "Alert.4259846": "Cc",
        "Alert.4259847": "Bcc",
        "Alert.4259848": "Subject",
        "Alert.4259849": "User_Agent",
        "Alert.4259850": "Cookie",
        "Alert.4259851": "Referer",
        "Alert.4259852": "Destination_Filename",
        "Alert.4259853": "Client_Version",
        "Alert.4259854": "Job_Name",
        "Alert.4259855": "Language",
        "Alert.4259856": "SWF_URL",
        "Alert.4259857": "TC_URL",
        "Alert.4259858": "RTMP_Application",
        "Alert.4259859": "Version",
        "Alert.4259860": "Local_User_Name",
        "Alert.4259867": "DNS_Name",
        "Alert.4259868": "SNMP_Item",
        "Alert.4259869": "Sensor_UUID",
        "Alert.4259870": "Process_Name",
        "Alert.4259871": "Source_Context",
        "Alert.4259872": "Target_Context",
        "Alert.4259873": "Description",
        "Alert.4259874": "SQL_Statement",
        "Alert.4259875": "From_Address",
        "Alert.4259876": "To_Address",
        "Alert.4259877": "File_Path",
        "Alert.4259878": "Target_Process_Name",
        "Alert.4259879": "Privileges",
        "Alert.4259880": "Search_Query",
        "Alert.4259881": "PCAP_Name",
        "Alert.4259882": "Vulnerability_References",
        "Alert.4259883": "Access_Privileges",
        "Alert.4259884": "Old_Value",
        "Alert.4259885": "New_Value",
        "Alert.4259886": "Device_URL",
        "Alert.4259887": "Engine_List",
        "Alert.4456449": "Num_Copies",
        "Alert.4456450": "Start_Page",
        "Alert.4456451": "End_Page",
        "Alert.4456457": "NTP_Offset_To_Monitor",
        "Alert.4456458": "Confidence",
        "Alert.4456459": "Hops",
        "Alert.4456460": "Priority",
        "Alert.54919169": "DNS - Type",
        "Alert.54919171": "Old_Reputation - ATD_File",
        "Alert.54919172": "New_Reputation - ATD_File",
        "Alert.65537": "Signature_Name",
        "Alert.65538": "Threat_Name",
        "Alert.65539": "Destination_Hostname",
        "Alert.65540": "Category",
        "Alert.65541": "Source_Zone",
        "Alert.65542": "Destination_Zone",
        "Alert.65543": "Target_Class",
        "Alert.65544": "Policy_Name",
        "Alert.65545": "Event_Class",
        "Alert.65546": "Request_Type",
        "Alert.65547": "Message_ID",
        "Alert.65548": "Mail_ID",
        "Alert.65549": "Recipient_ID",
        "Alert.65550": "Delivery_ID",
        "Alert.65551": "Creator_Name",
        "Alert.65552": "External_Application",
        "Alert.65553": "External_DB2_Server",
        "Alert.65554": "Table_Name",
        "Alert.65555": "Access_Resource",
        "Alert.65556": "Catalog_Name",
        "Alert.65557": "DB2_Plan_Name",
        "Alert.65558": "File_Type",
        "Alert.65559": "FTP_Command",
        "Alert.65560": "Job_Type",
        "Alert.65561": "Logical_Unit_Name",
        "Alert.65562": "LPAR_DB2_Subsystem",
        "Alert.65563": "Step_Count",
        "Alert.65564": "Step_Name",
        "Alert.65565": "Volume_ID",
        "Alert.65566": "Source_UserID",
        "Alert.65567": "Destination_UserID",
        "Alert.65568": "Mainframe_Job_Name",
        "Alert.65569": "Database_ID",
        "Alert.65570": "Malware_Insp_Action",
        "Alert.65571": "Malware_Insp_Result",
        "Alert.65572": "Source_Network",
        "Alert.65573": "Destination_Network",
        "Alert.65574": "Incoming_ID",
        "Alert.65575": "External_Hostname",
        "Alert.65576": "Area",
        "Alert.65577": "Facility",
        "Alert.65578": "Privileged_User",
        "Alert.65579": "Operating_System",
        "Alert.65580": "Logon_Type",
        "Alert.65581": "Management_Server",
        "Alert.65582": "External_SessionID",
        "Alert.65583": "Source_Logon_ID",
        "Alert.65584": "Destination_Logon_ID",
        "Alert.65585": "Session_Status",
        "Alert.65586": "URL_Category",
        "Alert.65587": "Caller_Process",
        "Alert.65588": "Registry_Key",
        "Alert.65589": "Registry_Value",
        "Alert.65590": "Mailbox",
        "Alert.65591": "Directory",
        "Alert.65592": "Destination_Directory",
        "Alert.65593": "SQL_Command",
        "Alert.65594": "Device_Action",
        "Alert.65595": "Threat_Category",
        "Alert.65596": "Threat_Handled",
        "Alert.65597": "Reason",
        "Alert.65599": "Detection_Method",
        "Alert.65600": "Virtual_Machine_Name",
        "Alert.65601": "Virtual_Machine_ID",
        "Alert.65602": "Datacenter_ID",
        "Alert.65603": "Datacenter_Name",
        "Alert.65604": "Interface_Dest",
        "Alert.65605": "Organizational_Unit",
        "Alert.65606": "External_Device_Type",
        "Alert.65607": "External_Device_ID",
        "Alert.65608": "External_Device_Name",
        "Alert.65609": "Service_Name",
        "Alert.65610": "Reputation_Name",
        "Alert.65611": "Status",
        "Alert.65612": "Sub_Status",
        "Alert.65613": "Web_Domain",
        "Alert.65614": "Group_Name",
        "Alert.65615": "App_Layer_Protocol",
        "Alert.65616": "Rule_Name",
        "Alert.65617": "Security_ID",
        "Alert.65618": "Authentication_Type",
        "Alert.65619": "SHA1",
        "Alert.65620": "File_ID",
        "Alert.65621": "Attribute_Type",
        "Alert.65622": "Access_Mask",
        "Alert.65623": "VPN_Feature_Name",
        "Alert.65624": "Hash",
        "Alert.65625": "Hash_Type",
        "Alert.65627": "Subcategory",
        "Alert.65628": "CnC_Host",
        "Alert.65629": "Share_Name",
        "Alert.65630": "SHA256",
        "Alert.71696385": "DNS - Type_Name",
        "Alert.71696387": "Old_Reputation - GTI_Cert",
        "Alert.71696388": "New_Reputation - GTI_Cert",
        "Alert.88473601": "DNS - Response_Code",
        "Alert.88473603": "Old_Reputation - TIE_Cert",
        "Alert.88473604": "New_Reputation - TIE_Cert",
        "Alert.ASNGeoDst": "ASNGeoDst",
        "Alert.ASNGeoSrc": "ASNGeoSrc",
        "Alert.Action": "Action",
        "Alert.AlertID": "AlertID",
        "Alert.AppIDCat": "AppIDCat",
        "Alert.AvgSeverity": "AvgSeverity",
        "Alert.BIN(1)": "AppID",
        "Alert.BIN(10)": "Object_Type",
        "Alert.BIN(11)": "Method",
        "Alert.BIN(12)": "File_Operation",
        "Alert.BIN(13)": "File_Operation_Succeeded",
        "Alert.BIN(14)": "User_Nickname",
        "Alert.BIN(15)": "Contact_Name",
        "Alert.BIN(16)": "Contact_Nickname",
        "Alert.BIN(17)": "DNS_Type",
        "Alert.BIN(18)": "DNS_Class",
        "Alert.BIN(19)": "Query_Response",
        "Alert.BIN(2)": "CommandID",
        "Alert.BIN(20)": "Authoritative_Answer",
        "Alert.BIN(21)": "SNMP_Operation",
        "Alert.BIN(22)": "SNMP_Item_Type",
        "Alert.BIN(23)": "SNMP_Version",
        "Alert.BIN(24)": "SNMP_Error_Code",
        "Alert.BIN(25)": "NTP_Client_Mode",
        "Alert.BIN(26)": "NTP_Server_Mode",
        "Alert.BIN(27)": "NTP_Request",
        "Alert.BIN(28)": "NTP_Opcode",
        "Alert.BIN(29)": "Interface",
        "Alert.BIN(3)": "DomainID",
        "Alert.BIN(30)": "Direction",
        "Alert.BIN(31)": "Sensor_Name",
        "Alert.BIN(32)": "Sensor_Type",
        "Alert.BIN(33)": "Response_Code",
        "Alert.BIN(34)": "Return_Code",
        "Alert.BIN(4)": "HostID",
        "Alert.BIN(5)": "ObjectID",
        "Alert.BIN(6)": "UserIDDst",
        "Alert.BIN(7)": "UserIDSrc",
        "Alert.BIN(8)": "Database_Name",
        "Alert.BIN(9)": "Application_Protocol",
        "Alert.CommandIDCat": "CommandIDCat",
        "Alert.DSID": "DSID",
        "Alert.DSIDSigID": "DSIDSigID",
        "Alert.DomainIDCat": "DomainIDCat",
        "Alert.DstIP": "DstIP",
        "Alert.DstMac": "DstMac",
        "Alert.DstPort": "DstPort",
        "Alert.EventCount": "EventCount",
        "Alert.FirstTime": "FirstTime",
        "Alert.Flow": "Flow",
        "Alert.FlowID": "FlowID",
        "Alert.GUIDDst": "GUIDDst",
        "Alert.GUIDSrc": "GUIDSrc",
        "Alert.HostIDCat": "HostIDCat",
        "Alert.IPSID": "IPSID",
        "Alert.IPSIDAlertID": "IPSIDAlertID",
        "Alert.LastTime": "LastTime",
        "Alert.LastTime_usec": "LastTime_usec",
        "Alert.ObjectIDCat": "ObjectIDCat",
        "Alert.Protocol": "Protocol",
        "Alert.RemCaseID": "RemCaseID",
        "Alert.RemOpenTicketTime": "RemOpenTicketTime",
        "Alert.Reviewed": "Reviewed",
        "Alert.Sequence": "Sequence",
        "Alert.SessionID": "SessionID",
        "Alert.Severity": "Severity",
        "Alert.SigID": "SigID",
        "Alert.SrcIP": "SrcIP",
        "Alert.SrcMac": "SrcMac",
        "Alert.SrcPort": "SrcPort",
        "Alert.Trusted": "Trusted",
        "Alert.UserFld10Cat": "UserFld10Cat",
        "Alert.UserFld21Cat": "UserFld21Cat",
        "Alert.UserFld22Cat": "UserFld22Cat",
        "Alert.UserFld23Cat": "UserFld23Cat",
        "Alert.UserFld24Cat": "UserFld24Cat",
        "Alert.UserFld25Cat": "UserFld25Cat",
        "Alert.UserFld26Cat": "UserFld26Cat",
        "Alert.UserFld27Cat": "UserFld27Cat",
        "Alert.UserFld8Cat": "UserFld8Cat",
        "Alert.UserFld9Cat": "UserFld9Cat",
        "Alert.UserIDDstCat": "UserIDDstCat",
        "Alert.UserIDSrcCat": "UserIDSrcCat",
        "Alert.VLan": "VLan",
        "Alert.WriteTime": "WriteTime",
        "Alert.ZoneDst": "ZoneDst",
        "Alert.ZoneSrc": "ZoneSrc",
    }
    """
    Fields name mapping.  
    """

    # NICKNAME TO INTERNAL NAMES
    SIEM_FIELDS_MAP_NICKNAME_TO_INTERNAL_NAME = {
        "ASNGeoDst": "Alert.ASNGeoDst",
        "ASNGeoSrc": "Alert.ASNGeoSrc",
        "Access_Mask": "Alert.65622",
        "Access_Privileges": "Alert.4259883",
        "Access_Resource": "Alert.65555",
        "Action": "Alert.Action",
        "Action.Name": "Action.Name",
        "Agent_GUID": "Alert.262162",
        "AlertID": "Alert.AlertID",
        "Analyzer_DAT_Version": "Alert.262170",
        "AppID": "Alert.BIN(1)",
        "AppIDCat": "Alert.AppIDCat",
        "App_Layer_Protocol": "Alert.65615",
        "Application_Protocol": "Alert.BIN(9)",
        "Area": "Alert.65576",
        "Attacker_IP": "Alert.262175",
        "Attribute_Type": "Alert.65621",
        "Authentication_Type": "Alert.65618",
        "Authoritative_Answer": "Alert.BIN(20)",
        "AvgSeverity": "Alert.AvgSeverity",
        "Bcc": "Alert.4259847",
        "Caller_Process": "Alert.65587",
        "Catalog_Name": "Alert.65556",
        "Category": "Alert.65540",
        "Cc": "Alert.4259846",
        "Class.Name": "Class.Name",
        "Class.Priority": "Class.Priority",
        "Client_Version": "Alert.4259853",
        "CnC_Host": "Alert.65628",
        "CommandID": "Alert.BIN(2)",
        "CommandIDCat": "Alert.CommandIDCat",
        "Confidence": "Alert.4456458",
        "Contact_Name": "Alert.BIN(15)",
        "Contact_Nickname": "Alert.BIN(16)",
        "Cookie": "Alert.4259850",
        "Creator_Name": "Alert.65551",
        "DAT_Version": "Alert.262165",
        "DB2_Plan_Name": "Alert.65557",
        "DNS - Class": "Alert.21364737",
        "DNS - Class_Name": "Alert.38141953",
        "DNS - Query": "Alert.122028033",
        "DNS - Response_Code": "Alert.88473601",
        "DNS - Response_Code_Name": "Alert.105250817",
        "DNS - Type": "Alert.54919169",
        "DNS - Type_Name": "Alert.71696385",
        "DNS_Class": "Alert.BIN(18)",
        "DNS_Name": "Alert.4259867",
        "DNS_Server_IP": "Alert.262178",
        "DNS_Type": "Alert.BIN(17)",
        "DSID": "Alert.DSID",
        "DSIDSigID": "Alert.DSIDSigID",
        "Database_GUID": "Alert.262169",
        "Database_ID": "Alert.65569",
        "Database_Name": "Alert.BIN(8)",
        "Datacenter_ID": "Alert.65602",
        "Datacenter_Name": "Alert.65603",
        "Delivery_ID": "Alert.65550",
        "Description": "Alert.4259873",
        "Destination_Directory": "Alert.65592",
        "Destination_Filename": "Alert.4259852",
        "Destination_Hostname": "Alert.65539",
        "Destination_Logon_ID": "Alert.65584",
        "Destination_Network": "Alert.65573",
        "Destination_UserID": "Alert.65567",
        "Destination_Zone": "Alert.65542",
        "Detection_Method": "Alert.65599",
        "Device_Action": "Alert.65594",
        "Device_Confidence": "Alert.262179",
        "Device_IP": "Alert.262154",
        "Device_Port": "Alert.262155",
        "Device_URL": "Alert.4259886",
        "Direction": "Alert.BIN(30)",
        "Directory": "Alert.65591",
        "DomainID": "Alert.BIN(3)",
        "DomainIDCat": "Alert.DomainIDCat",
        "DstIP": "Alert.DstIP",
        "DstMac": "Alert.DstMac",
        "DstPort": "Alert.DstPort",
        "End_Page": "Alert.4456451",
        "Engine_List": "Alert.4259887",
        "EventCount": "Alert.EventCount",
        "Event_Class": "Alert.65545",
        "External_Application": "Alert.65552",
        "External_DB2_Server": "Alert.65553",
        "External_Device_ID": "Alert.65607",
        "External_Device_Name": "Alert.65608",
        "External_Device_Type": "Alert.65606",
        "External_EventID": "Alert.262156",
        "External_Hostname": "Alert.65575",
        "External_SessionID": "Alert.65582",
        "External_SubEventID": "Alert.262158",
        "FTP_Command": "Alert.65559",
        "Facility": "Alert.65577",
        "File_Hash": "Alert.262159",
        "File_ID": "Alert.65620",
        "File_Operation": "Alert.BIN(12)",
        "File_Operation_Succeeded": "Alert.BIN(13)",
        "File_Path": "Alert.4259877",
        "File_Type": "Alert.65558",
        "Filename": "Alert.4259843",
        "FirstTime": "Alert.FirstTime",
        "Flow": "Alert.Flow",
        "FlowID": "Alert.FlowID",
        "From": "Alert.4259844",
        "From_Address": "Alert.4259875",
        "GUIDDst": "Alert.GUIDDst",
        "GUIDSrc": "Alert.GUIDSrc",
        "GeoLoc_ASNGeoDst.Latitude": "GeoLoc_ASNGeoDst.Latitude",  # This is useless
        "GeoLoc_ASNGeoDst.Longitude": "GeoLoc_ASNGeoDst.Longitude",  # This is useless
        "GeoLoc_ASNGeoDst.Msg": "GeoLoc_ASNGeoDst.Msg",  # This is useless
        "GeoLoc_ASNGeoDst.XCoord": "GeoLoc_ASNGeoDst.XCoord",  # This is useless
        "GeoLoc_ASNGeoDst.YCoord": "GeoLoc_ASNGeoDst.YCoord",  # This is useless
        "GeoLoc_ASNGeoSrc.Latitude": "GeoLoc_ASNGeoSrc.Latitude",  # This is useless
        "GeoLoc_ASNGeoSrc.Longitude": "GeoLoc_ASNGeoSrc.Longitude",  # This is useless
        "GeoLoc_ASNGeoSrc.Msg": "GeoLoc_ASNGeoSrc.Msg",  # This is useless
        "GeoLoc_ASNGeoSrc.XCoord": "GeoLoc_ASNGeoSrc.XCoord",  # This is useless
        "GeoLoc_ASNGeoSrc.YCoord": "GeoLoc_ASNGeoSrc.YCoord",  # This is useless
        "Grid_Master_IP": "Alert.262153",
        "Group_Name": "Alert.65614",
        "Handheld_ID": "Alert.262168",
        "Handle_ID": "Alert.262160",
        "Hash": "Alert.65624",
        "Hash_Type": "Alert.65625",
        "Hops": "Alert.4456459",
        "HostID": "Alert.BIN(4)",
        "HostIDCat": "Alert.HostIDCat",
        "IPS.Name": "IPS.Name",
        "IPSID": "Alert.IPSID",
        "IPSIDAlertID": "Alert.IPSIDAlertID",
        "Incident_ID": "Alert.262173",
        "Incoming_ID": "Alert.65574",
        "Instance_GUID": "Alert.262161",
        "Interface": "Alert.BIN(29)",
        "Interface_Dest": "Alert.65604",
        "Job_Name": "Alert.4259854",
        "Job_Type": "Alert.65560",
        "LPAR_DB2_Subsystem": "Alert.65562",
        "Language": "Alert.4259855",
        "LastTime": "Alert.LastTime",
        "LastTime_usec": "Alert.LastTime_usec",
        "Local_User_Name": "Alert.4259860",
        "Logical_Unit_Name": "Alert.65561",
        "Logon_Type": "Alert.65580",
        "Mail_ID": "Alert.65548",
        "Mailbox": "Alert.65590",
        "Mainframe_Job_Name": "Alert.65568",
        "Malware_Insp_Action": "Alert.65570",
        "Malware_Insp_Result": "Alert.65571",
        "Management_Server": "Alert.65581",
        "Message_ID": "Alert.65547",
        "Message_Text": "Alert.4259842",
        "Method": "Alert.BIN(11)",
        "NAT_Details": "Alert.262146",
        "NTP_Client_Mode": "Alert.BIN(25)",
        "NTP_Offset_To_Monitor": "Alert.4456457",
        "NTP_Opcode": "Alert.BIN(28)",
        "NTP_Request": "Alert.BIN(27)",
        "NTP_Server_Mode": "Alert.BIN(26)",
        "New_Reputation - ATD_File": "Alert.54919172",
        "New_Reputation - GTI_Cert": "Alert.71696388",
        "New_Reputation - GTI_File": "Alert.21364740",
        "New_Reputation - TIE_Cert": "Alert.88473604",
        "New_Reputation - TIE_File": "Alert.38141956",
        "New_Value": "Alert.4259885",
        "Num_Copies": "Alert.4456449",
        "ObjectID": "Alert.BIN(5)",
        "ObjectIDCat": "Alert.ObjectIDCat",
        "Object_GUID": "Alert.262176",
        "Object_Type": "Alert.BIN(10)",
        "Old_Reputation - ATD_File": "Alert.54919171",
        "Old_Reputation - GTI_Cert": "Alert.71696387",
        "Old_Reputation - GTI_File": "Alert.21364739",
        "Old_Reputation - TIE_Cert": "Alert.88473603",
        "Old_Reputation - TIE_File": "Alert.38141955",
        "Old_Value": "Alert.4259884",
        "Operating_System": "Alert.65579",
        "Organizational_Unit": "Alert.65605",
        "PCAP_Name": "Alert.4259881",
        "PID": "Alert.262152",
        "Parent_File_Hash": "Alert.262172",
        "Policy_ID": "Alert.262167",
        "Policy_Name": "Alert.65544",
        "Priority": "Alert.4456460",
        "Privileged_User": "Alert.65578",
        "Privileges": "Alert.4259879",
        "Process_Name": "Alert.4259870",
        "Protocol": "Alert.Protocol",
        "Query_Response": "Alert.BIN(19)",
        "Queue_ID": "Alert.196609",
        "RTMP_Application": "Alert.4259858",
        "Reason": "Alert.65597",
        "Recipient_ID": "Alert.65549",
        "Referer": "Alert.4259851",
        "Registry - Key": "Alert.21364738",
        "Registry - Value": "Alert.38141954",
        "Registry_Key": "Alert.65588",
        "Registry_Value": "Alert.65589",
        "RemCaseID": "Alert.RemCaseID",
        "RemOpenTicketTime": "Alert.RemOpenTicketTime",
        "Reputation": "Alert.262164",
        "Reputation_Name": "Alert.65610",
        "Reputation_Score": "Alert.262171",
        "Reputation_Server_IP": "Alert.262177",
        "Request_Type": "Alert.65546",
        "Response_Code": "Alert.BIN(33)",
        "Response_Time": "Alert.262145",
        "Return_Code": "Alert.BIN(34)",
        "Reviewed": "Alert.Reviewed",
        "Rule.ID": "Rule.ID",
        "Rule.NormID": "Rule.NormID",
        "Rule.msg": "Rule.msg",
        "Rule_NDSNormSigID.msg": "Rule_NDSNormSigID.msg",
        "Rule_Name": "Alert.65616",
        "SHA1": "Alert.65619",
        "SHA256": "Alert.65630",
        "SNMP_Error_Code": "Alert.BIN(24)",
        "SNMP_Item": "Alert.4259868",
        "SNMP_Item_Type": "Alert.BIN(22)",
        "SNMP_Operation": "Alert.BIN(21)",
        "SNMP_Version": "Alert.BIN(23)",
        "SQL_Command": "Alert.65593",
        "SQL_Statement": "Alert.4259874",
        "SWF_URL": "Alert.4259856",
        "Search_Query": "Alert.4259880",
        "Security_ID": "Alert.65617",
        "Sensor_Name": "Alert.BIN(31)",
        "Sensor_Type": "Alert.BIN(32)",
        "Sensor_UUID": "Alert.4259869",
        "Sequence": "Alert.Sequence",
        "Server_ID": "Alert.262166",
        "Service_Name": "Alert.65609",
        "SessionID": "Alert.SessionID",
        "Session_Status": "Alert.65585",
        "Severity": "Alert.Severity",
        "Share_Name": "Alert.65629",
        "SigID": "Alert.SigID",
        "Signature_Name": "Alert.65537",
        "Source_Context": "Alert.4259871",
        "Source_Logon_ID": "Alert.65583",
        "Source_Network": "Alert.65572",
        "Source_UserID": "Alert.65566",
        "Source_Zone": "Alert.65541",
        "Spam_Score": "Alert.262157",
        "SrcIP": "Alert.SrcIP",
        "SrcMac": "Alert.SrcMac",
        "SrcPort": "Alert.SrcPort",
        "Start_Page": "Alert.4456450",
        "Status": "Alert.65611",
        "Step_Count": "Alert.65563",
        "Step_Name": "Alert.65564",
        "Sub_Status": "Alert.65612",
        "Subcategory": "Alert.65627",
        "Subject": "Alert.4259848",
        "TC_URL": "Alert.4259857",
        "Table_Name": "Alert.65554",
        "Target_Class": "Alert.65543",
        "Target_Context": "Alert.4259872",
        "Target_Process_Name": "Alert.4259878",
        "ThirdPartyType.Name": "ThirdPartyType.Name",  # This is useless
        "Threat_Category": "Alert.65595",
        "Threat_Handled": "Alert.65596",
        "Threat_Name": "Alert.65538",
        "To": "Alert.4259845",
        "To_Address": "Alert.4259876",
        "Trusted": "Alert.Trusted",
        "URL": "Alert.4259841",
        "URL_Category": "Alert.65586",
        "UUID": "Alert.262163",
        "UserFld10Cat": "Alert.UserFld10Cat",
        "UserFld21Cat": "Alert.UserFld21Cat",
        "UserFld22Cat": "Alert.UserFld22Cat",
        "UserFld23Cat": "Alert.UserFld23Cat",
        "UserFld24Cat": "Alert.UserFld24Cat",
        "UserFld25Cat": "Alert.UserFld25Cat",
        "UserFld26Cat": "Alert.UserFld26Cat",
        "UserFld27Cat": "Alert.UserFld27Cat",
        "UserFld8Cat": "Alert.UserFld8Cat",
        "UserFld9Cat": "Alert.UserFld9Cat",
        "UserIDDst": "Alert.BIN(6)",
        "UserIDDstCat": "Alert.UserIDDstCat",
        "UserIDSrc": "Alert.BIN(7)",
        "UserIDSrcCat": "Alert.UserIDSrcCat",
        "User_Agent": "Alert.4259849",
        "User_Nickname": "Alert.BIN(14)",
        "Users.Name": "Users.Name",  # This is useless
        "VLan": "Alert.VLan",
        "VPN_Feature_Name": "Alert.65623",
        "Version": "Alert.4259859",
        "Victim_IP": "Alert.262174",
        "Virtual_Machine_ID": "Alert.65601",
        "Virtual_Machine_Name": "Alert.65600",
        "Volume_ID": "Alert.65565",
        "Vulnerability_References": "Alert.4259882",
        "Web_Domain": "Alert.65613",
        "WriteTime": "Alert.WriteTime",
        "ZoneDst": "Alert.ZoneDst",
        "ZoneSrc": "Alert.ZoneSrc",
        "Zone_ZoneDst.Name": "Zone_ZoneDst.Name",  # This is useless
        "Zone_ZoneSrc.Name": "Zone_ZoneSrc.Name",
    }  # This is useless
    """
    Fields name mapping (reversed).  
    """

    def __init__(self, *args, **kwargs):
        """
        Create a new event representation

        Arguments:
            - `adict` (`dict`): Event parameters
            - `id` (`str`): The event ``"IPSIDAlertID"`` to instanciate. Will load informations.  
        """
        super().__init__(*args, **kwargs)

    def _find_key(self, key):
        """
        Use the fields name mapping to resolve internal name based on nickname
        """
        if collections.UserDict.__contains__(self, key):
            return key
        if (
            key in self.SIEM_FIELDS_MAP_NICKNAME_TO_INTERNAL_NAME.keys()
            and collections.UserDict.__contains__(
                self, self.SIEM_FIELDS_MAP_NICKNAME_TO_INTERNAL_NAME[key]
            )
        ):
            return self.SIEM_FIELDS_MAP_NICKNAME_TO_INTERNAL_NAME[key]

        # Loop thought FIELDS_TABLES and try with table prefix
        # Old behaviour
        for table in self.FIELDS_TABLES:
            if collections.UserDict.__contains__(self, table + "." + key):
                return table + "." + key

        raise KeyError("Dictionnary key not found : {}".format(key))

    def __getitem__(self, key):
        """
        Use the fields name mapping to offer better dict usage
        """
        return collections.UserDict.__getitem__(self, self._find_key(key))
    
    def __delitem__(self, key):
        """
        Use the fields name mapping to offer better dict usage
        """
        return collections.UserDict.__delitem__(self, self._find_key(key))

    def __contains__(self, key):
        """
        Use the fields name mapping to offer better dict usage
        """
        try:
            return self._find_key(key) != None
        except KeyError:
            return False
    
    def __setitem__(self, key, value):
        """
        Use the fields name mapping to offer better dict usage
        """
        try:
            return collections.UserDict.__setitem__(self, self._find_key(key), value)
        except KeyError:
            return collections.UserDict.__setitem__(self, key, value)

    def get_id(self):
        """
        Get the event ID.  

        Return the full event ID or `None`.  
        """
        the_id = (
            self.data["Alert.IPSIDAlertID"]
            if ("Alert.IPSIDAlertID" in self.data)
            else str(self.data["ipsId"]["id"]) + "|" + str(self.data["alertId"])
            if ("alertId" in self.data)
            else self.data["eventId"]
            if ("eventId" in self.data)
            else None
        )
        if the_id:
            return the_id
        else:
            return None

    def clear_notes(self):
        """
        Replace the notes by an empty string. Desctructive action.
        """
        self.set_note("", no_date=True)

    def set_note(self, note, no_date=False):
        """
        Set the event's note. Desctructive action.  

        Note: 
            Uses the internal API method `IPS_ADDALERTNOTE`
        """
        the_id = self.get_id()

        if isinstance(the_id, str):

            if len(note) >= 4000:
                log.warning(
                    "The note is longer than 4000 characters, only the"
                    "first 4000 characters will be kept. The maximum"
                    "accepted by the SIEM is 4096 characters."
                )
                note = note[:4000] + "\n\n--NOTE HAS BEEN TRUNCATED--"

            if no_date == False:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                note = note.replace('"', '\\"').replace("\n", "\\n")
                note = timestamp + " - " + note

            self.nitro.request("add_note_to_event_int", id=the_id, note=note)
        else:
            log.error(
                "Couldn't set event's note, the event ID hasn't been found. Event: {}".format(
                    self
                )
            )

    def data_from_id(self, id, use_query=False, extra_fields=[]):
        """
        Load event's data.

        Arguments:
            - `id` (`str`): The event ID. (i.e. : ``"144128388087414784|747122896"``)
            - `use_query` (`bool`): Uses the query module to retreive common event data. Only works with SIEM 11.2 or greater.
                Default behaviour will call ``ipsGetAlertData`` to retreive the complete event definition.
            - `extra_fields` (`list`): Only when `use_query=True`. Additionnal event fields to load in the query.
        """

        if use_query == True:
            f = FieldFilter("IPSIDAlertID", id, operator="EQUALS")
            e = EventManager(
                time_range="CUSTOM",
                start_time=datetime.now() - timedelta(days=365),
                end_time=datetime.now() + timedelta(days=1),
                filters=[f],
                fields=extra_fields,
                limit=2,
            )
            try:
                e.load_data()
            except NitroError:
                log.error(
                    "Query failed, can't load event's data from id with 1 year timerange, looking at the last 45 days only..."
                )
                e.start_time = datetime.now() - timedelta(days=45)
                e.load_data()

            if len(e) == 1:
                return e[0]
            else:
                raise NitroError(
                    "Could not load event : "
                    + str(id)
                    + " from query :"
                    + str(e.__dict__)
                    + ". Try with use_query=False."
                )

        elif use_query == False:
            return self.nitro.request("get_alert_data", id=id)

    def refresh(self, use_query=None, extra_fields=None):
        """
        Re-load event's data.

        Arguments:
            - `use_query` (`bool`): Force the use of the query module to retreive the event data. Only works with SIEM 11.2 or greater.
                In contrario, if explicitly `False`, force the use of ``ipsGetAlertData`` to get the details.
                Default behaviour will use the query module if an ``'Alert.IPSIDAlertID'`` keys exists.  
            - `extra_fields` (`list`): Only when `use_query=True` or the Event is already a query event. Additionnal event fields to load in the query.

        Warning:
            Enforce `use_query=True` will reset the Events fields to whatever is passed to `extra_fields`

        Raises:
            `AttributeError` if the event ID has not been found.
        """
        if not self.get_id():
            raise AttributeError(
                "Can't refresh a Event without an ID: {}".format(self.data)
            )
        if use_query == None:
            if "Alert.IPSIDAlertID" in self.data.keys():
                # ensure to re-use the query module if that's the case
                self.data.update(
                    self.data_from_id(
                        self.data["Alert.IPSIDAlertID"],
                        use_query=True,
                        extra_fields=self.data.keys() + extra_fields
                        if extra_fields
                        else [],
                    )
                )
            else:
                the_id = self.get_id()
                self.data.update(self.data_from_id(the_id))
        elif use_query:
            self.data.update(
                self.data_from_id(
                    self.get_id(),
                    use_query=True,
                    extra_fields=extra_fields if extra_fields else [],
                )
            )
        else:
            the_id = self.get_id()
            self.data.update(self.data_from_id(the_id))


class GroupedEvent(Event):
    """
    Dict-Like object. Represents a row of grouped query results.

    Common keys:

    - The requested field
    - ``COUNT(*)``: The number of event for the result row
    - ``SUM(Alert.EventCount)``:  The sum of their `EventCount` attribute


    The following `__getitem__` key mapping are added on top of `Event`'s ::

        "Count":"COUNT(*)",
        "TotalEventCount":"SUM(Alert.EventCount)"

    Meaning that you can use ``e['TotalEventCount']``, it will return ``e['SUM(Alert.EventCount)']``.

    Note:
        `GroupedEvent` is NOT suitable for Event's operations like `Event.set_note` or `Event.refresh` because there is no ID associated with events records.

    """

    SIEM_FIELDS_MAP_NICKNAME_TO_INTERNAL_NAME = (
        Event.SIEM_FIELDS_MAP_NICKNAME_TO_INTERNAL_NAME
    )
    SIEM_FIELDS_MAP_NICKNAME_TO_INTERNAL_NAME["Count"] = "COUNT(*)"
    SIEM_FIELDS_MAP_NICKNAME_TO_INTERNAL_NAME[
        "TotalEventCount"
    ] = "SUM(Alert.EventCount)"


class _QueryFilter(collections.UserDict):
    """Base class for all SIEM query objects in order to dump the filter as dict."""


class GroupFilter(_QueryFilter):
    """
    Query group filter

    Based on ``EsmFilterGroup`` SIEM API object.  

    Used to dump groups of filters in the right format.

    See:
        Object `FieldFilter`
    """

    def __init__(self, filters, logic="AND"):
        """
        Create a new group filter

        Arguments:
            - `filters` (`list`): a list of filters. Filters can be `msiempy.FieldFilter` or `msiempy.GroupFilter`
            - `logic` (`str`): ``"AND"`` or ``"OR"``
        """
        super().__init__()

        # Declaring attributes
        self.data = {
            "type": "EsmFilterGroup",
            "filters": [dict(f) for f in filters],
            "logic": logic,
        }


class FieldFilter(_QueryFilter):
    """
    Query field filter
    
    Based on ``EsmFieldFilter`` SIEM API object.  

    This class is automatically used when instanciating `EventManager` objects. It automatically creates filters in the right `dict` format from ``tuples`` passed to the filter argument of `EventManager`:
    
    >>> e = EventManager(time_range='LAST_MINUTE', filters=[ ('SrcIP', ['10.5.0.0/16']) ])

    Default operator is ``"IN"``. To change the operator, create s custom `FieldFilter`.

    Exemple to filter by Signature ID:
    
    >>> e = EventManager(time_range='LAST_24_HOURS', filters=[ FieldFilter('DSIDSigID', ["49190-4294967295"], operator='EQUALS') ])

    Note:
        Make sure the filter name is valid by checking the result of `EventManager.get_possible_filters` or use the provided script in the sample folder

    See:
        Object `GroupFilter`
    """

    # Declaring static value containing all the possibles
    # event fields usable in filters should be loaded once, when instanciating a FieldFilter

    # Basically [ item.get('name') for item in EventManager().get_possible_filters() ]
    DOCUMENTED_FILTERS = [
        "IPSID",  # IPSID has been manually added to this list
        "IPSIDAlertID",  # IPSIDAlertID has been manually added to this list
        "AppID",
        "CommandID",
        "DomainID",
        "HostID",
        "ObjectID",
        "UserIDDst",
        "UserIDSrc",
        "URL",
        "Database_Name",
        "Message_Text",
        "Response_Time",
        "Application_Protocol",
        "Object_Type",
        "Filename",
        "From",
        "To",
        "Cc",
        "Bcc",
        "Subject",
        "Method",
        "User_Agent",
        "Cookie",
        "Referer",
        "File_Operation",
        "File_Operation_Succeeded",
        "Destination_Filename",
        "User_Nickname",
        "Contact_Name",
        "Contact_Nickname",
        "Client_Version",
        "Job_Name",
        "Language",
        "SWF_URL",
        "TC_URL",
        "RTMP_Application",
        "Version",
        "Local_User_Name",
        "NAT_Details",
        "Network_Layer",
        "Transport_Layer",
        "Session_Layer",
        "Application_Layer",
        "HTTP_Layer",
        "HTTP_Req_URL",
        "HTTP_Req_Cookie",
        "HTTP_Req_Referer",
        "HTTP_Req_Host",
        "HTTP_Req_Method",
        "HTTP_User_Agent",
        "DNS_Name",
        "DNS_Type",
        "DNS_Class",
        "Query_Response",
        "Authoritative_Answer",
        "SNMP_Operation",
        "SNMP_Item_Type",
        "SNMP_Version",
        "SNMP_Error_Code",
        "NTP_Client_Mode",
        "NTP_Server_Mode",
        "NTP_Request",
        "NTP_Opcode",
        "SNMP_Item",
        "Interface",
        "Direction",
        "Sensor_Name",
        "Sensor_UUID",
        "Sensor_Type",
        "Signature_Name",
        "Threat_Name",
        "Destination_Hostname",
        "Category",
        "Process_Name",
        "Grid_Master_IP",
        "Response_Code",
        "Device_Port",
        "Device_IP",
        "PID",
        "Target_Context",
        "Source_Context",
        "Target_Class",
        "Policy_Name",
        "Destination_Zone",
        "Source_Zone",
        "Queue_ID",
        "Delivery_ID",
        "Recipient_ID",
        "Spam_Score",
        "Mail_ID",
        "To_Address",
        "From_Address",
        "Message_ID",
        "Request_Type",
        "SQL_Statement",
        "External_EventID",
        "Event_Class",
        "Description",
        "File_Hash",
        "Mainframe_Job_Name",
        "External_SubEventID",
        "Destination_UserID",
        "Source_UserID",
        "Volume_ID",
        "Step_Name",
        "Step_Count",
        "LPAR_DB2_Subsystem",
        "Logical_Unit_Name",
        "Job_Type",
        "FTP_Command",
        "File_Type",
        "DB2_Plan_Name",
        "Catalog_Name",
        "Access_Resource",
        "Table_Name",
        "External_DB2_Server",
        "External_Application",
        "Creator_Name",
        "Return_Code",
        "Database_ID",
        "Incoming_ID",
        "Handle_ID",
        "Destination_Network",
        "Source_Network",
        "Malware_Insp_Result",
        "Malware_Insp_Action",
        "External_Hostname",
        "Privileged_User",
        "Facility",
        "Area",
        "Instance_GUID",
        "Logon_Type",
        "Operating_System",
        "File_Path",
        "Agent_GUID",
        "Reputation",
        "URL_Category",
        "Session_Status",
        "Destination_Logon_ID",
        "Source_Logon_ID",
        "UUID",
        "External_SessionID",
        "Management_Server",
        "Detection_Method",
        "Target_Process_Name",
        "Analyzer_DAT_Version",
        "Forwarding_Status",
        "Reason",
        "Threat_Handled",
        "Threat_Category",
        "Device_Action",
        "Database_GUID",
        "SQL_Command",
        "Destination_Directory",
        "Directory",
        "Mailbox",
        "Handheld_ID",
        "Policy_ID",
        "Server_ID",
        "Registry_Value",
        "Registry_Key",
        "Caller_Process",
        "DAT_Version",
        "Interface_Dest",
        "Datacenter_Name",
        "Datacenter_ID",
        "Virtual_Machine_ID",
        "Virtual_Machine_Name",
        "PCAP_Name",
        "Search_Query",
        "Service_Name",
        "External_Device_Name",
        "External_Device_ID",
        "External_Device_Type",
        "Organizational_Unit",
        "Privileges",
        "Reputation_Name",
        "Vulnerability_References",
        "Web_Domain",
        "Sub_Status",
        "Status",
        "Access_Privileges",
        "Rule_Name",
        "App_Layer_Protocol",
        "Group_Name",
        "Authentication_Type",
        "New_Value",
        "Old_Value",
        "Security_ID",
        "SHA1",
        "Reputation_Score",
        "Parent_File_Hash",
        "File_ID",
        "Engine_List",
        "Device_URL",
        "Attacker_IP",
        "Victim_IP",
        "Incident_ID",
        "Attribute_Type",
        "Access_Mask",
        "Object_GUID",
        "VPN_Feature_Name",
        "Reputation_Server_IP",
        "DNS_Server_IP",
        "Hash_Type",
        "Hash",
        "Subcategory",
        "Wireless_SSID",
        "Share_Name",
        "CnC_Host",
        "Device_Confidence",
        "SHA256",
        "DSIDSigID",
        "ZoneSrc",
        "Action",
        "ASNGeoDst",
        "FirstTime",
        "SrcPort",
        "AvgSeverity",
        "DSID",
        "DstPort",
        "SrcIP",
        "ZoneDst",
        "SigID",
        "GUIDSrc",
        "GUIDDst",
        "DstIP",
        "ID",
        "Protocol",
        "NormID",
        "SrcMac",
        "SessionID",
        "ASNGeoSrc",
        "DstMac",
        "LastTime",
    ]
    """ List fo documented filter names, show a warning if trying to filter on a unknown filter name """

    def __init__(self, name, values, operator="IN"):
        """
        Create a new field filter for a query.  

        Arguments:
            - `name` (`str`): field name as string. Field name property. Example : ``"SrcIP"``. See full list here: https://github.com/mfesiem/msiempy/blob/master/static/all_filters.json
            - `values` (`list`): list of values the field is going to be tested againts with the specified orperator.
            - `orperator` (`str`): One of: ``IN``, ``NOT_IN``, ``GREATER_THAN``, ``LESS_THAN``, ``GREATER_OR_EQUALS_THAN``, ``LESS_OR_EQUALS_THAN``, ``NUMERIC_EQUALS``, ``NUMERIC_NOT_EQUALS``, ``DOES_NOT_EQUAL``, ``EQUALS``, ``CONTAINS``, ``DOES_NOT_CONTAIN``, ``REGEX``
        """
        super().__init__()

        # Declaring attributes
        self._operator = str()
        self._values = list()

        self.operator = operator
        self.values = values

        self.name = name
        """
        Name of the field
        """

        self.data = {
            "type": "EsmFieldFilter",
            "field": {"name": self.name},
            "operator": self.operator,
            "values": self.values,
        }

        # check the name against the list of possible filters and log warning if not present.
        if name not in FieldFilter.DOCUMENTED_FILTERS:
            log.warning(
                "You're using an undocumented filter name: '{name}'.  ".format(
                    name=name
                )
            )

    POSSIBLE_OPERATORS = [
        "IN",
        "NOT_IN",
        "GREATER_THAN",
        "LESS_THAN",
        "GREATER_OR_EQUALS_THAN",
        "LESS_OR_EQUALS_THAN",
        "NUMERIC_EQUALS",
        "NUMERIC_NOT_EQUALS",
        "DOES_NOT_EQUAL",
        "EQUALS",
        "CONTAINS",
        "DOES_NOT_CONTAIN",
        "REGEX",
    ]
    """List of possibles operators"""

    POSSIBLE_VALUE_TYPES = [
        {"type": "EsmWatchlistValue", "key": "watchlist"},
        {"type": "EsmVariableValue", "key": "variable"},
        {"type": "EsmBasicValue", "key": "value"},
        {"type": "EsmCompoundValue", "key": "values"},
    ]
    """
    List of possible value type. See `add_value`.
    """

    def _get_operator(self):
        return self._operator

    def _set_operator(self, operator):
        if operator in self.POSSIBLE_OPERATORS:
            self._operator = operator
        else:
            raise AttributeError(
                "Illegal value for the filter operator: "
                + str(operator)
                + ". The operator must be in "
                + str(self.POSSIBLE_OPERATORS)
            )
    
    operator = property(fget=_get_operator, fset=_set_operator)
    """Filter operator.
    Throws `AttributeError` if trying to set an unknown operator.
    """

    def _get_values(self):
        
        return self._values

    def _set_values(self, values):
        if isinstance(values, list):

            for val in values:
                if isinstance(val, dict):
                    self.add_value(**val)

                elif isinstance(val, (int, float, str)):
                    self.add_basic_value(val)

                else:
                    raise TypeError(
                        "Invalid filter type, must be a list, int, float or str"
                    )

        elif isinstance(values, dict):
            self.add_value(**values)

        elif isinstance(values, (int, float, str)):
            self.add_basic_value(values)

        else:
            raise TypeError("Invalid filter type, must be a list, int, float or str")
    
    values = property(fget=_get_values, fset=_set_values)
    """
    List of values of the filter.

    Values will be added with:

    - `add_value` if value is a `dict`
    - `add_basic_value` if value type is `int`, `float` or `str`.

    Values will always be added to the filter. To remove values, handle directly the `_values` property.

    Example::

        filter = FieldFilter(name='DstIP', values=[{'type':'EsmWatchlistValue', 'watchlist':42}], operator='IN')
    """

    def add_value(self, type=None, **kwargs):
        """
        Add a new value to the filter.

        Arguments:  
            - `type` (`str`): Type of the value
            - `value` (`str`): If ``type`` is ``"EsmBasicValue"``
            - `watchlist` (`int`): If ``type`` is ``"EsmWatchlistValue"``
            - `variable` (`int`): If ``type`` is ``"EsmVariableValue"``
            - `values` (`list`): If ``type`` is ``"EsmCompoundValue"``

        Raises: 
            `KeyError` or `AttributeError` if you don't respect the correct type/key/value combo.

        Note: 
            Filtering query with other type of filter than ``EsmBasicValue`` is not tested.
        """
        try:
            type_template = None

            # Look for the type of the object ex EsmBasicValue
            # it' used to know the type and name of value parameter we should receive next
            for possible_value_type in self.POSSIBLE_VALUE_TYPES:
                if possible_value_type["type"] == type:
                    type_template = possible_value_type
                    if type != "EsmBasicValue":
                        log.warning(
                            "Filtering query with other type of filter than 'EsmBasicValue' is not tested."
                        )
                    break

            # Error throwing
            if type_template != None:
                if type_template["key"] in kwargs:

                    # Adds a new value to a fields filter
                    # Filtering query with other type of filter than 'EsmBasicValue' is not tested.
                    value = kwargs[type_template["key"]]
                    if type == "EsmBasicValue":
                        value = str(value)
                        # log.debug('Adding a basic value to filter ('+self.text+') : '+value)
                    self._values.append({"type": type, type_template["key"]: value})
                    # log.debug('The value was appended to the list: '+str(self))

                # Error throwing
                else:
                    raise KeyError("The valid key value argument is not present")
            else:
                raise KeyError("Impossible filter")
        except KeyError as err:
            raise AttributeError(
                "You must provide a valid named Arguments containing the type and values for this filter. The type/keys must be in "
                + str(self.POSSIBLE_VALUE_TYPES)
                + "Can't be type="
                + str(type)
                + " "
                + str(kwargs)
                + ". Additionnal indicator :"
                + str(err)
            )

    def add_basic_value(self, value):
        """
        Wrapper arround `add_value` method to simply add a ``EsmBasicValue``.
        """
        self.add_value(type="EsmBasicValue", value=value)
