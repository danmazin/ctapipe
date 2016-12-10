"""
low-level utility functions for dealing with data files
"""

import os
from os.path import basename, splitext, dirname, join, exists
# from astropy import log
import numpy as np
from copy import deepcopy
from ctapipe.core import Component
from traitlets import Unicode, Int, CaselessStrEnum, observe


def get_file_type(filename):
    """
    Returns a string with the type of the given file (guessed from the
    extension). The '.gz' or '.bz2' compression extensions are
    ignored.

    >>> get_file_type('myfile.fits.gz')
    'fits'

    """
    root, ext = os.path.splitext(filename)
    if ext in ['.gz', '.bz2']:
        ext = os.path.splitext(root)[1]

    ext = ext[1:]  # strip off leading '.'

    # special cases:
    if ext in ['fit', 'FITS', 'FIT']:
        ext = 'fits'

    return ext


# Placed here to avoid error from recursive import
from ctapipe.utils.datasets import get_path
from ctapipe.io.hessio import hessio_event_source


def targetio_source(filepath, max_events=None, allowed_tels=None,
                    requested_event=None, use_event_id=False):
    """
    Temporary function to return a "source" generator from a targetio file,
    only if targetpipe exists on this python interpreter.

    Parameters
    ----------
    filepath : string
        Filepath for the input targetio file
    max_events : int
        Maximum number of events to read
    allowed_tels : list[int]
        select only a subset of telescope, if None, all are read.
    requested_event : int
        Seek to a paricular event index
    use_event_id : bool
        If True ,'requested_event' now seeks for a particular event id instead
        of index

    Returns
    -------
    source : generator
        A generator that can be iterated over to obtain events, obtained from
        a targetio file.
    """

    # Check targetpipe is installed
    try:
        import importlib
        targetpipe_spec = importlib.util.find_spec("targetpipe")
        found = targetpipe_spec is not None
        if found:
            from targetpipe.io.targetio import targetio_event_source
            return targetio_event_source(filepath, max_events=max_events,
                                         allowed_tels=allowed_tels,
                                         requested_event=requested_event,
                                         use_event_id=use_event_id)
        else:
            raise RuntimeError()
    except RuntimeError:
        # log.exception("targetpipe is not installed on this interpreter")
        raise


class FileReader(Component):
    """
    Class to handle generic input files. Enables obtaining the "source"
    generator, regardless of the type of file (either hessio or camera file).

    Attributes
    ----------
    input_path : str
    directory : str
        Automatically set from `input_path`.
    filename : str
        Name of the file without the extension.
        Automatically set from `input_path`.
    extension : str
        Automatically set from `input_path`.
    output_directory : str
        Directory to save outputs for this file

    """
    name = 'FileReader'

    possible_origins = ['hessio', 'targetio']

    input_path = Unicode(get_path('gamma_test.simtel.gz'),
                         help='Path to the input file containing '
                              'events.').tag(config=True)
    origin = CaselessStrEnum(possible_origins, 'hessio',
                             help='Origin of the input file.').tag(config=True)
    max_events = Int(None, allow_none=True,
                     help='Maximum number of events that will be read from'
                          'the file').tag(config=True)

    def __init__(self, config, tool, **kwargs):
        """
        Class to handle generic input files. Enables obtaining the "source"
        generator, regardless of the type of file (either hessio or camera
        file).

        Parameters
        ----------
        config : traitlets.loader.Config
            Configuration specified by config file or cmdline arguments.
            Used to set traitlet values.
            Set to None if no configuration to pass.
        tool : ctapipe.core.Tool
            Tool executable that is calling this component.
            Passes the correct logger to the component.
            Set to None if no Tool to pass.
        kwargs
        """
        super().__init__(config=config, parent=tool, **kwargs)
        self._num_events = None
        self._event_id_list = []

        self._init_path(self.input_path)

    def _init_path(self, input_path):
        print(input_path)
        if not exists(input_path):
            raise FileNotFoundError("file path does not exist: '{}'"
                                    .format(input_path))

        self.input_path = input_path
        self.directory = dirname(input_path)
        self.filename = splitext(basename(input_path))[0]
        self.extension = splitext(input_path)[1]
        self.output_directory = join(self.directory, self.filename)

        if self.log:
            self.log.info("INPUT PATH = {}".format(self.input_path))
            self.log.info("ORIGIN = {}".format(self.origin))

    @observe('input_path')
    def on_input_path_changed(self, change):
        new = change['new']
        try:
            self.log.warning("Change: input_path={}".format(change))
            self._num_events = None
            self._event_id_list = []
            self._init_path(new)
        except AttributeError:
            pass

    @observe('origin')
    def on_origin_changed(self, change):
        try:
            self.log.warning("Change: origin={}".format(change))
        except AttributeError:
            pass

    @observe('max_events')
    def on_max_events_changed(self, change):
        try:
            self.log.warning("Change: max_events={}".format(change))
            self._num_events = None
            self._event_id_list = []
        except AttributeError:
            pass

    @property
    def num_events(self):
        self.log.info("Obtaining number of events in file...")
        first_event = self.get_event(0)
        if self._num_events:
            pass
        elif 'num_events' in first_event.meta:
            self._num_events = first_event.meta['num_events']
        else:
            self._num_events = len(self.event_id_list)
        if self.max_events is not None and self._num_events > self.max_events:
            self._num_events = self.max_events
        self.log.info("Number of events inside file = {}"
                      .format(self._num_events))
        return self._num_events

    @property
    def event_id_list(self):
        self.log.info("Retrieving list of event ids...")
        if self._event_id_list:
            pass
        else:
            self.log.info("Building new list of event ids...")
            source = self.read()
            for event in source:
                self._event_id_list.append(event.dl0.event_id)
        self.log.info("List of event ids retrieved.")
        return self._event_id_list

    def read(self, allowed_tels=None, requested_event=None,
             use_event_id=False):
        """
        Read the file using the appropriate method depending on the file origin

        Parameters
        ----------
        allowed_tels : list[int]
            select only a subset of telescope, if None, all are read. This can
            be used for example emulate the final CTA data format, where there
            would be 1 telescope per file (whereas in current monte-carlo,
            they are all interleaved into one file)
        requested_event : int
            Seek to a paricular event index
        use_event_id : bool
            If True ,'requested_event' now seeks for a particular event id
            instead of index

        Returns
        -------
        source : generator
            A generator that can be iterated over to obtain events
        """

        # Obtain relevent source
        self.log.debug("Reading file...")
        if self.max_events:
            self.log.info("Max events being read = {}".format(self.max_events))
        switch = {
            'hessio':
                lambda: hessio_event_source(get_path(self.input_path),
                                            max_events=self.max_events,
                                            allowed_tels=allowed_tels,
                                            requested_event=requested_event,
                                            use_event_id=use_event_id),
            'targetio':
                lambda: targetio_source(self.input_path,
                                        max_events=self.max_events,
                                        allowed_tels=allowed_tels,
                                        requested_event=requested_event,
                                        use_event_id=use_event_id),
        }
        try:
            source = switch[self.origin]()
        except KeyError:
            self.log.exception("unknown file origin '{}'".format(self.origin))
            raise
        self.log.debug("File reading complete")

        return source

    def get_event(self, requested_event, use_event_id=False):
        """
        Loop through events until the requested event is found

        Parameters
        ----------
        requested_event : int
            Seek to a paricular event index
        use_event_id : bool
            If True ,'requested_event' now seeks for a particular event id
            instead of index

        Returns
        -------
        event : `ctapipe` event-container

        """
        source = self.read(requested_event=requested_event,
                           use_event_id=use_event_id)
        event = next(source)
        return deepcopy(event)

    def find_max_true_npe(self, telescopes=None):
        """
        Loop through events to find the maximum true npe

        Parameters
        ----------
        telescopes : list
            List of telecopes to include. If None, then all telescopes
            are included.

        Returns
        -------
        max_pe : int

        """
        self.log.info("Finding maximum true npe inside file...")
        source = self.read()
        max_pe = 0
        for event in source:
            tels = list(event.dl0.tels_with_data)
            if telescopes is not None:
                tels = []
                for tel in telescopes:
                    if tel in event.dl0.tels_with_data:
                        tels.append(tel)
            if event.count == 0:
                # Check events have true charge included
                try:
                    if np.all(event.mc.tel[tels[0]].photo_electron_image == 0):
                        raise KeyError
                except KeyError:
                    self.log.exception('[chargeres] Source does not contain '
                                       'true charge')
                    raise
            for telid in tels:
                pe = event.mc.tel[telid].photo_electron_image
                this_max = np.max(pe)
                if this_max > max_pe:
                    max_pe = this_max
        self.log.info("Maximum true npe inside file = {}".format(max_pe))

        return max_pe
