"""datamanager.py - input output for AnuGA


This module takes care of reading and writing datafiles such as topograhies,
model output, etc


Formats used within AnuGA:

.sww: Netcdf format for storing model output f(t,x,y)
.tms: Netcdf format for storing time series f(t)

.csv: ASCII format for storing arbitrary points and associated attributes
.pts: NetCDF format for storing arbitrary points and associated attributes

.asc: ASCII format of regular DEMs as output from ArcView
.prj: Associated ArcView file giving more meta data for asc format
.ers: ERMapper header format of regular DEMs for ArcView

.dem: NetCDF representation of regular DEM data

.tsh: ASCII format for storing meshes and associated boundary and region info
.msh: NetCDF format for storing meshes and associated boundary and region info

.nc: Native ferret NetCDF format
.geo: Houdinis ascii geometry format (?)


A typical dataflow can be described as follows

Manually created files:
ASC, PRJ:     Digital elevation models (gridded)
TSH:          Triangular meshes (e.g. created from anuga.pmesh)
NC            Model outputs for use as boundary conditions (e.g from MOST)


AUTOMATICALLY CREATED FILES:

ASC, PRJ  ->  DEM  ->  PTS: Conversion of DEM's to native pts file

NC -> SWW: Conversion of MOST bundary files to boundary sww

PTS + TSH -> TSH with elevation: Least squares fit

TSH -> SWW:  Conversion of TSH to sww viewable using Swollen

TSH + Boundary SWW -> SWW: Simluation using abstract_2d_finite_volumes

"""

import exceptions
class TitleValueError(exceptions.Exception): pass
class DataMissingValuesError(exceptions.Exception): pass
class DataFileNotOpenError(exceptions.Exception): pass
class DataTimeError(exceptions.Exception): pass
class DataDomainError(exceptions.Exception): pass
class NewQuantity(exceptions.Exception): pass



import csv
import os, sys
import shutil
from struct import unpack
import array as p_array
#import time, os
from os import sep, path, remove, mkdir, access, F_OK, W_OK, getcwd


from Numeric import concatenate, array, Float, Int, Int32, resize, sometrue, \
     searchsorted, zeros, allclose, around, reshape, transpose, sort, \
     NewAxis, ArrayType, compress, take, arange, argmax, alltrue,shape,Float

import string

from Scientific.IO.NetCDF import NetCDFFile
#from shutil import copy
from os.path import exists, basename, join
from os import getcwd


from anuga.coordinate_transforms.redfearn import redfearn, \
     convert_from_latlon_to_utm
from anuga.coordinate_transforms.geo_reference import Geo_reference, \
     write_NetCDF_georeference, ensure_geo_reference
from anuga.geospatial_data.geospatial_data import Geospatial_data,\
     ensure_absolute
from anuga.config import minimum_storable_height as default_minimum_storable_height
from anuga.config import max_float
from anuga.utilities.numerical_tools import ensure_numeric,  mean
from anuga.caching.caching import myhash
from anuga.utilities.anuga_exceptions import ANUGAError
from anuga.shallow_water import Domain
from anuga.abstract_2d_finite_volumes.pmesh2domain import \
     pmesh_to_domain_instance
from anuga.abstract_2d_finite_volumes.util import get_revision_number, \
     remove_lone_verts, sww2timeseries, get_centroid_values
from anuga.load_mesh.loadASCII import export_mesh_file
# formula mappings

quantity_formula = {'momentum':'(xmomentum**2 + ymomentum**2)**0.5',
                    'depth':'stage-elevation',
                    'speed': \
 '(xmomentum**2 + ymomentum**2)**0.5/(stage-elevation+1.e-6/(stage-elevation))'}


    
def make_filename(s):
    """Transform argument string into a Sexsuitable filename
    """

    s = s.strip()
    s = s.replace(' ', '_')
    s = s.replace('(', '')
    s = s.replace(')', '')
    s = s.replace('__', '_')

    return s


def check_dir(path, verbose=None):
    """Check that specified path exists.
    If path does not exist it will be created if possible

    USAGE:
       checkdir(path, verbose):

    ARGUMENTS:
        path -- Directory
        verbose -- Flag verbose output (default: None)

    RETURN VALUE:
        Verified path including trailing separator

    """

    import os.path

    if sys.platform in ['nt', 'dos', 'win32', 'what else?']:
        unix = 0
    else:
        unix = 1


    if path[-1] != os.sep:
        path = path + os.sep  # Add separator for directories

    path = os.path.expanduser(path) # Expand ~ or ~user in pathname
    if not (os.access(path,os.R_OK and os.W_OK) or path == ''):
        try:
            exitcode=os.mkdir(path)

            # Change access rights if possible
            #
            if unix:
                exitcode=os.system('chmod 775 '+path)
            else:
                pass  # FIXME: What about acces rights under Windows?

            if verbose: print 'MESSAGE: Directory', path, 'created.'

        except:
            print 'WARNING: Directory', path, 'could not be created.'
            if unix:
                path = '/tmp/'
            else:
                path = 'C:'

            print 'Using directory %s instead' %path

    return(path)



def del_dir(path):
    """Recursively delete directory path and all its contents
    """

    import os

    if os.path.isdir(path):
        for file in os.listdir(path):
            X = os.path.join(path, file)


            if os.path.isdir(X) and not os.path.islink(X):
                del_dir(X)
            else:
                try:
                    os.remove(X)
                except:
                    print "Could not remove file %s" %X

        os.rmdir(path)
        
        
# ANOTHER OPTION, IF NEED IN THE FUTURE, Nick B 7/2007    
def rmgeneric(path, __func__,verbose=False):
    ERROR_STR= """Error removing %(path)s, %(error)s """

    try:
        __func__(path)
        if verbose: print 'Removed ', path
    except OSError, (errno, strerror):
        print ERROR_STR % {'path' : path, 'error': strerror }
            
def removeall(path,verbose=False):

    if not os.path.isdir(path):
        return
    
    files=os.listdir(path)

    for x in files:
        fullpath=os.path.join(path, x)
        if os.path.isfile(fullpath):
            f=os.remove
            rmgeneric(fullpath, f)
        elif os.path.isdir(fullpath):
            removeall(fullpath)
            f=os.rmdir
            rmgeneric(fullpath, f,verbose)



def create_filename(datadir, filename, format, size=None, time=None):

    import os
    #from anuga.config import data_dir

    FN = check_dir(datadir) + filename

    if size is not None:
        FN += '_size%d' %size

    if time is not None:
        FN += '_time%.2f' %time

    FN += '.' + format
    return FN


def get_files(datadir, filename, format, size):
    """Get all file (names) with given name, size and format
    """

    import glob

    import os
    #from anuga.config import data_dir

    dir = check_dir(datadir)

    pattern = dir + os.sep + filename + '_size=%d*.%s' %(size, format)
    return glob.glob(pattern)



#Generic class for storing output to e.g. visualisation or checkpointing
class Data_format:
    """Generic interface to data formats
    """


    def __init__(self, domain, extension, mode = 'w'):
        assert mode in ['r', 'w', 'a'], '''Mode %s must be either:''' %mode +\
                                        '''   'w' (write)'''+\
                                        '''   'r' (read)''' +\
                                        '''   'a' (append)'''

        #Create filename
        self.filename = create_filename(domain.get_datadir(),
                                        domain.get_name(), extension)

        #print 'F', self.filename
        self.timestep = 0
        self.domain = domain
        


        # Exclude ghosts in case this is a parallel domain
        self.number_of_nodes = domain.number_of_full_nodes        
        self.number_of_volumes = domain.number_of_full_triangles
        #self.number_of_volumes = len(domain)        




        #FIXME: Should we have a general set_precision function?



#Class for storing output to e.g. visualisation
class Data_format_sww(Data_format):
    """Interface to native NetCDF format (.sww) for storing model output

    There are two kinds of data

    1: Constant data: Vertex coordinates and field values. Stored once
    2: Variable data: Conserved quantities. Stored once per timestep.

    All data is assumed to reside at vertex locations.
    """


    def __init__(self, domain, mode = 'w',\
                 max_size = 2000000000,
                 recursion = False):
        from Scientific.IO.NetCDF import NetCDFFile
        from Numeric import Int, Float, Float32

        self.precision = Float32 #Use single precision for quantities
        if hasattr(domain, 'max_size'):
            self.max_size = domain.max_size #file size max is 2Gig
        else:
            self.max_size = max_size
        self.recursion = recursion
        self.mode = mode

        Data_format.__init__(self, domain, 'sww', mode)

        if hasattr(domain, 'minimum_storable_height'):
            self.minimum_storable_height = domain.minimum_storable_height
        else:
            self.minimum_storable_height = default_minimum_storable_height

        # NetCDF file definition
        fid = NetCDFFile(self.filename, mode)

        if mode == 'w':
            description = 'Output from anuga.abstract_2d_finite_volumes suitable for plotting'
            self.writer = Write_sww()
            self.writer.store_header(fid,
                                     domain.starttime,
                                     self.number_of_volumes,
                                     self.domain.number_of_full_nodes,
                                     description=description,
                                     smoothing=domain.smooth,
                                     order=domain.default_order)

            # Extra optional information
            if hasattr(domain, 'texture'):
                fid.texture = domain.texture

            if domain.quantities_to_be_monitored is not None:
                fid.createDimension('singleton', 1)
                fid.createDimension('two', 2)                

                poly = domain.monitor_polygon
                if poly is not None:
                    N = len(poly)
                    fid.createDimension('polygon_length', N)
                    fid.createVariable('extrema.polygon',
                                       self.precision,
                                       ('polygon_length',
                                        'two'))
                    fid.variables['extrema.polygon'][:] = poly                                    

                    
                interval = domain.monitor_time_interval
                if interval is not None:
                    fid.createVariable('extrema.time_interval',
                                       self.precision,
                                       ('two',))
                    fid.variables['extrema.time_interval'][:] = interval

                
                for q in domain.quantities_to_be_monitored:
                    #print 'doing', q
                    fid.createVariable(q+'.extrema', self.precision,
                                       ('numbers_in_range',))
                    fid.createVariable(q+'.min_location', self.precision,
                                       ('numbers_in_range',))
                    fid.createVariable(q+'.max_location', self.precision,
                                       ('numbers_in_range',))
                    fid.createVariable(q+'.min_time', self.precision,
                                       ('singleton',))
                    fid.createVariable(q+'.max_time', self.precision,
                                       ('singleton',))

                    
        fid.close()


    def store_connectivity(self):
        """Specialisation of store_connectivity for net CDF format

        Writes x,y,z coordinates of triangles constituting
        the bed elevation.
        """

        from Scientific.IO.NetCDF import NetCDFFile

        from Numeric import concatenate, Int

        domain = self.domain

        #Get NetCDF
        fid = NetCDFFile(self.filename, 'a')  #Open existing file for append

        # Get the variables
        x = fid.variables['x']
        y = fid.variables['y']
        z = fid.variables['elevation']

        volumes = fid.variables['volumes']

        # Get X, Y and bed elevation Z
        Q = domain.quantities['elevation']
        X,Y,Z,V = Q.get_vertex_values(xy=True,
                                      precision=self.precision)

        #
        points = concatenate( (X[:,NewAxis],Y[:,NewAxis]), axis=1 )
        self.writer.store_triangulation(fid,
                                        points,
                                        V.astype(volumes.typecode()),
                                        Z,
                                        points_georeference= \
                                        domain.geo_reference)

        # Close
        fid.close()


    def store_timestep(self, names):
        """Store time and named quantities to file
        """
        from Scientific.IO.NetCDF import NetCDFFile
        import types
        from time import sleep
        from os import stat

        from Numeric import choose
        
        # Get NetCDF       
        retries = 0
        file_open = False
        while not file_open and retries < 10:
            try:
                fid = NetCDFFile(self.filename, 'a') # Open existing file
            except IOError:
                # This could happen if someone was reading the file.
                # In that case, wait a while and try again
                msg = 'Warning (store_timestep): File %s could not be opened'\
                      %self.filename
                msg += ' - trying step %s again' %self.domain.time
                print msg
                retries += 1
                sleep(1)
            else:
                file_open = True

        if not file_open:
            msg = 'File %s could not be opened for append' %self.filename
            raise DataFileNotOpenError, msg



        # Check to see if the file is already too big:
        time = fid.variables['time']
        i = len(time)+1
        file_size = stat(self.filename)[6]
        file_size_increase =  file_size/i
        if file_size + file_size_increase > self.max_size*(2**self.recursion):
            # In order to get the file name and start time correct,
            # I change the domain.filename and domain.starttime.
            # This is the only way to do this without changing
            # other modules (I think).

            # Write a filename addon that won't break swollens reader
            # (10.sww is bad)
            filename_ext = '_time_%s'%self.domain.time
            filename_ext = filename_ext.replace('.', '_')
            
            # Remember the old filename, then give domain a
            # name with the extension
            old_domain_filename = self.domain.get_name()
            if not self.recursion:
                self.domain.set_name(old_domain_filename+filename_ext)


            # Change the domain starttime to the current time
            old_domain_starttime = self.domain.starttime
            self.domain.starttime = self.domain.time

            # Build a new data_structure.
            next_data_structure=\
                Data_format_sww(self.domain, mode=self.mode,\
                                max_size = self.max_size,\
                                recursion = self.recursion+1)
            if not self.recursion:
                print '    file_size = %s'%file_size
                print '    saving file to %s'%next_data_structure.filename
            #set up the new data_structure
            self.domain.writer = next_data_structure

            #FIXME - could be cleaner to use domain.store_timestep etc.
            next_data_structure.store_connectivity()
            next_data_structure.store_timestep(names)
            fid.sync()
            fid.close()

            #restore the old starttime and filename
            self.domain.starttime = old_domain_starttime
            self.domain.set_name(old_domain_filename)            
        else:
            self.recursion = False
            domain = self.domain

            # Get the variables
            time = fid.variables['time']
            stage = fid.variables['stage']
            xmomentum = fid.variables['xmomentum']
            ymomentum = fid.variables['ymomentum']
            i = len(time)
            if type(names) not in [types.ListType, types.TupleType]:
                names = [names]

            if 'stage' in names and 'xmomentum' in names and \
               'ymomentum' in names:

                # Get stage
                Q = domain.quantities['stage']
                A,_ = Q.get_vertex_values(xy = False,
                                          precision = self.precision)                
                z = fid.variables['elevation']
                stage = choose(A-z[:] >= self.minimum_storable_height,
                           (z[:], A))
                
                # Get xmomentum
                Q = domain.quantities['xmomentum']
                xmomentum, _ = Q.get_vertex_values(xy = False,
                                          precision = self.precision)
                
                # Get ymomentum
                Q = domain.quantities['ymomentum']
                ymomentum, _ = Q.get_vertex_values(xy = False,
                                          precision = self.precision)
                
                # Write quantities to NetCDF
                self.writer.store_quantities(fid, 
                                             time=self.domain.time,
                                             precision=self.precision,
                                             stage=stage,
                                             xmomentum=xmomentum,
                                             ymomentum=ymomentum)
            else:
                # This is producing a sww that is not standard.
                # Store time
                time[i] = self.domain.time
                
                for name in names:
                    # Get quantity
                    Q = domain.quantities[name]
                    A,V = Q.get_vertex_values(xy = False,
                                              precision = self.precision)

                    # FIXME: Make this general (see below)
                    if name == 'stage':
                        z = fid.variables['elevation']
                        A = choose(A-z[:] >= self.minimum_storable_height,
                                   (z[:], A))
                        stage[i,:] = A.astype(self.precision)
                    elif name == 'xmomentum':
                        xmomentum[i,:] = A.astype(self.precision)
                    elif name == 'ymomentum':
                        ymomentum[i,:] = A.astype(self.precision)

                   #As in....
                   #eval( name + '[i,:] = A.astype(self.precision)' )
                   #FIXME (Ole): But we need a UNIT test for that before
                   # refactoring



            # Update extrema if requested
            domain = self.domain
            if domain.quantities_to_be_monitored is not None:
                for q, info in domain.quantities_to_be_monitored.items():

                    if info['min'] is not None:
                        fid.variables[q + '.extrema'][0] = info['min']
                        fid.variables[q + '.min_location'][:] =\
                                        info['min_location']
                        fid.variables[q + '.min_time'][0] = info['min_time']
                        
                    if info['max'] is not None:
                        fid.variables[q + '.extrema'][1] = info['max']
                        fid.variables[q + '.max_location'][:] =\
                                        info['max_location']
                        fid.variables[q + '.max_time'][0] = info['max_time']

            

            #Flush and close
            fid.sync()
            fid.close()



# Class for handling checkpoints data
class Data_format_cpt(Data_format):
    """Interface to native NetCDF format (.cpt)
    """


    def __init__(self, domain, mode = 'w'):
        from Scientific.IO.NetCDF import NetCDFFile
        from Numeric import Int, Float, Float

        self.precision = Float #Use full precision

        Data_format.__init__(self, domain, 'sww', mode)


        # NetCDF file definition
        fid = NetCDFFile(self.filename, mode)

        if mode == 'w':
            #Create new file
            fid.institution = 'Geoscience Australia'
            fid.description = 'Checkpoint data'
            #fid.smooth = domain.smooth
            fid.order = domain.default_order

            # dimension definitions
            fid.createDimension('number_of_volumes', self.number_of_volumes)
            fid.createDimension('number_of_vertices', 3)

            #Store info at all vertices (no smoothing)
            fid.createDimension('number_of_points', 3*self.number_of_volumes)
            fid.createDimension('number_of_timesteps', None) #extensible

            # variable definitions

            #Mesh
            fid.createVariable('x', self.precision, ('number_of_points',))
            fid.createVariable('y', self.precision, ('number_of_points',))


            fid.createVariable('volumes', Int, ('number_of_volumes',
                                                'number_of_vertices'))

            fid.createVariable('time', self.precision,
                               ('number_of_timesteps',))

            #Allocate space for all quantities
            for name in domain.quantities.keys():
                fid.createVariable(name, self.precision,
                                   ('number_of_timesteps',
                                    'number_of_points'))

        #Close
        fid.close()


    def store_checkpoint(self):
        """
        Write x,y coordinates of triangles.
        Write connectivity (
        constituting
        the bed elevation.
        """

        from Scientific.IO.NetCDF import NetCDFFile

        from Numeric import concatenate

        domain = self.domain

        #Get NetCDF
        fid = NetCDFFile(self.filename, 'a')  #Open existing file for append

        # Get the variables
        x = fid.variables['x']
        y = fid.variables['y']

        volumes = fid.variables['volumes']

        # Get X, Y and bed elevation Z
        Q = domain.quantities['elevation']
        X,Y,Z,V = Q.get_vertex_values(xy=True,
                      precision = self.precision)



        x[:] = X.astype(self.precision)
        y[:] = Y.astype(self.precision)
        z[:] = Z.astype(self.precision)

        volumes[:] = V

        #Close
        fid.close()


    def store_timestep(self, name):
        """Store time and named quantity to file
        """
        from Scientific.IO.NetCDF import NetCDFFile
        from time import sleep

        #Get NetCDF
        retries = 0
        file_open = False
        while not file_open and retries < 10:
            try:
                fid = NetCDFFile(self.filename, 'a') #Open existing file
            except IOError:
                #This could happen if someone was reading the file.
                #In that case, wait a while and try again
                msg = 'Warning (store_timestep): File %s could not be opened'\
                  %self.filename
                msg += ' - trying again'
                print msg
                retries += 1
                sleep(1)
            else:
                file_open = True

        if not file_open:
            msg = 'File %s could not be opened for append' %self.filename
            raise DataFileNotOPenError, msg


        domain = self.domain

        # Get the variables
        time = fid.variables['time']
        stage = fid.variables['stage']
        i = len(time)

        #Store stage
        time[i] = self.domain.time

        # Get quantity
        Q = domain.quantities[name]
        A,V = Q.get_vertex_values(xy=False,
                                  precision = self.precision)

        stage[i,:] = A.astype(self.precision)

        #Flush and close
        fid.sync()
        fid.close()


#### NED is national exposure database (name changed to NEXIS)
    
LAT_TITLE = 'LATITUDE'
LONG_TITLE = 'LONGITUDE'
X_TITLE = 'x'
Y_TITLE = 'y'
class Exposure_csv:
    def __init__(self,file_name, latitude_title=LAT_TITLE,
                 longitude_title=LONG_TITLE, is_x_y_locations=None,
                 x_title=X_TITLE, y_title=Y_TITLE,
                 refine_polygon=None, title_check_list=None):
        """
        This class is for handling the exposure csv file.
        It reads the file in and converts the lats and longs to a geospatial
        data object.
        Use the methods to read and write columns.

        The format of the csv files it reads is;
           The first row is a title row.
           comma's are the delimiters
           each column is a 'set' of data

        Feel free to use/expand it to read other csv files. 
           
           
        It is not for adding and deleting rows
        
        Can geospatial handle string attributes? It's not made for them.
        Currently it can't load and save string att's.

        So just use geospatial to hold the x, y and georef? Bad, since
        different att's are in diferent structures.  Not so bad, the info
        to write if the .csv file is saved is in attribute_dic

        The location info is in the geospatial attribute.
        
        
        """
        self._file_name = file_name
        self._geospatial = None #

        # self._attribute_dic is a dictionary.
        #The keys are the column titles.
        #The values are lists of column data
        
        # self._title_index_dic is a dictionary.
        #The keys are the column titles.
        #The values are the index positions of file columns.
        self._attribute_dic, self._title_index_dic = \
            csv2dict(self._file_name, title_check_list=title_check_list)
        try:
            #Have code here that handles caps or lower 
            lats = self._attribute_dic[latitude_title]
            longs = self._attribute_dic[longitude_title]
            
        except KeyError:
            # maybe a warning..
            #Let's see if this works..
            if False != is_x_y_locations:
                is_x_y_locations = True
            pass
        else:
            self._geospatial = Geospatial_data(latitudes = lats,
                 longitudes = longs)

        if is_x_y_locations is True:
            if self._geospatial is not None:
                pass #fixme throw an error
            try:
                xs = self._attribute_dic[x_title]
                ys = self._attribute_dic[y_title]
                points = [[float(i),float(j)] for i,j in map(None,xs,ys)]
            except KeyError:
                # maybe a warning..
                msg = "Could not find location information."
                raise TitleValueError, msg
            else:
                self._geospatial = Geospatial_data(data_points=points)
            
        # create a list of points that are in the refining_polygon
        # described by a list of indexes representing the points

    def __cmp__(self, other):
        #print "self._attribute_dic",self._attribute_dic
        #print "other._attribute_dic",other._attribute_dic
        #print "self._title_index_dic", self._title_index_dic
        #print "other._title_index_dic", other._title_index_dic
        
        #check that a is an instance of this class
        if isinstance(self, type(other)):
            result = cmp(self._attribute_dic, other._attribute_dic)
            if result <>0:
                return result
            # The order of the columns is important. Therefore.. 
            result = cmp(self._title_index_dic, other._title_index_dic)
            if result <>0:
                return result
            for self_ls, other_ls in map(None,self._attribute_dic, \
                    other._attribute_dic):
                result = cmp(self._attribute_dic[self_ls],
                             other._attribute_dic[other_ls])
                if result <>0:
                    return result
            return 0
        else:
            return 1
    

    def get_column(self, column_name, use_refind_polygon=False):
        """
        Given a column name return a list of the column values

        Note, the type of the values will be String!
        do this to change a list of strings to a list of floats
        time = [float(x) for x in time]
        
        Not implemented:
        if use_refind_polygon is True, only return values in the
        refined polygon
        """
        if not self._attribute_dic.has_key(column_name):
            msg = 'Therer is  no column called %s!' %column_name
            raise TitleValueError, msg
        return self._attribute_dic[column_name]


    def get_value(self, value_column_name,
                  known_column_name,
                  known_values,
                  use_refind_polygon=False):
        """
        Do linear interpolation on the known_colum, using the known_value,
        to return a value of the column_value_name.
        """
        pass


    def get_location(self, use_refind_polygon=False):
        """
        Return a geospatial object which describes the
        locations of the location file.

        Note, if there is not location info, this returns None.
        
        Not implemented:
        if use_refind_polygon is True, only return values in the
        refined polygon
        """
        return self._geospatial

    def set_column(self, column_name, column_values, overwrite=False):
        """
        Add a column to the 'end' (with the right most column being the end)
        of the csv file.

        Set overwrite to True if you want to overwrite a column.
        
        Note, in column_name white space is removed and case is not checked.
        Precondition
        The column_name and column_values cannot have comma's in it.
        """
        
        value_row_count = \
            len(self._attribute_dic[self._title_index_dic.keys()[0]])
        if len(column_values) <> value_row_count: 
            msg = 'The number of column values must equal the number of rows.'
            raise DataMissingValuesError, msg
        
        if self._attribute_dic.has_key(column_name):
            if not overwrite:
                msg = 'Column name %s already in use!' %column_name
                raise TitleValueError, msg
        else:
            # New title.  Add it to the title index.
            self._title_index_dic[column_name] = len(self._title_index_dic)
        self._attribute_dic[column_name] = column_values
        #print "self._title_index_dic[column_name]",self._title_index_dic 

    def save(self, file_name=None):
        """
        Save the exposure csv file
        """
        if file_name is None:
            file_name = self._file_name
        
        fd = open(file_name,'wb')
        writer = csv.writer(fd)
        
        #Write the title to a cvs file
        line = [None]* len(self._title_index_dic)
        for title in self._title_index_dic.iterkeys():
            line[self._title_index_dic[title]]= title
        writer.writerow(line)
        
        # Write the values to a cvs file
        value_row_count = \
            len(self._attribute_dic[self._title_index_dic.keys()[0]])
        for row_i in range(value_row_count):
            line = [None]* len(self._title_index_dic)
            for title in self._title_index_dic.iterkeys():
                line[self._title_index_dic[title]]= \
                     self._attribute_dic[title][row_i]
            writer.writerow(line)


def csv2dict(file_name, title_check_list=None):
    """
    Load in the csv as a dic, title as key and column info as value, .
    Also, create a dic, title as key and column index as value,
    to keep track of the column order. 

    Two dictionaries are returned.
    
    WARNING: Vaules are returned as strings.
    do this to change a list of strings to a list of floats
        time = [float(x) for x in time]

        
    """
    
    #
    attribute_dic = {}
    title_index_dic = {}
    titles_stripped = [] # list of titles
    reader = csv.reader(file(file_name))

    # Read in and manipulate the title info
    titles = reader.next()
    for i,title in enumerate(titles):
        titles_stripped.append(title.strip())
        title_index_dic[title.strip()] = i
    title_count = len(titles_stripped)       
    #print "title_index_dic",title_index_dic
    if title_check_list is not None:
        for title_check in title_check_list:
            #msg = "Reading error.  This row is not present ", title_check 
            #assert title_index_dic.has_key(title_check), msg
            if not title_index_dic.has_key(title_check):
                #reader.close()
                msg = "Reading error.  This row is not present ", \
                      title_check                     
                raise IOError, msg
                
    
    
    #create a dic of colum values, indexed by column title
    for line in reader:
        if len(line) <> title_count:
            raise IOError #FIXME make this nicer
        for i, value in enumerate(line):
            attribute_dic.setdefault(titles_stripped[i],[]).append(value)
        
    return attribute_dic, title_index_dic


#Auxiliary
def write_obj(filename,x,y,z):
    """Store x,y,z vectors into filename (obj format)
       Vectors are assumed to have dimension (M,3) where
       M corresponds to the number elements.
       triangles are assumed to be disconnected

       The three numbers in each vector correspond to three vertices,

       e.g. the x coordinate of vertex 1 of element i is in x[i,1]

    """
    #print 'Writing obj to %s' % filename

    import os.path

    root, ext = os.path.splitext(filename)
    if ext == '.obj':
        FN = filename
    else:
        FN = filename + '.obj'


    outfile = open(FN, 'wb')
    outfile.write("# Triangulation as an obj file\n")

    M, N = x.shape
    assert N==3  #Assuming three vertices per element

    for i in range(M):
        for j in range(N):
            outfile.write("v %f %f %f\n" % (x[i,j],y[i,j],z[i,j]))

    for i in range(M):
        base = i*N
        outfile.write("f %d %d %d\n" % (base+1,base+2,base+3))

    outfile.close()


#########################################################
#Conversion routines
########################################################

def sww2obj(basefilename, size):
    """Convert netcdf based data output to obj
    """
    from Scientific.IO.NetCDF import NetCDFFile

    from Numeric import Float, zeros

    #Get NetCDF
    FN = create_filename('.', basefilename, 'sww', size)
    print 'Reading from ', FN
    fid = NetCDFFile(FN, 'r')  #Open existing file for read


    # Get the variables
    x = fid.variables['x']
    y = fid.variables['y']
    z = fid.variables['elevation']
    time = fid.variables['time']
    stage = fid.variables['stage']

    M = size  #Number of lines
    xx = zeros((M,3), Float)
    yy = zeros((M,3), Float)
    zz = zeros((M,3), Float)

    for i in range(M):
        for j in range(3):
            xx[i,j] = x[i+j*M]
            yy[i,j] = y[i+j*M]
            zz[i,j] = z[i+j*M]

    #Write obj for bathymetry
    FN = create_filename('.', basefilename, 'obj', size)
    write_obj(FN,xx,yy,zz)


    #Now read all the data with variable information, combine with
    #x,y info and store as obj

    for k in range(len(time)):
        t = time[k]
        print 'Processing timestep %f' %t

        for i in range(M):
            for j in range(3):
                zz[i,j] = stage[k,i+j*M]


        #Write obj for variable data
        #FN = create_filename(basefilename, 'obj', size, time=t)
        FN = create_filename('.', basefilename[:5], 'obj', size, time=t)
        write_obj(FN,xx,yy,zz)


def dat2obj(basefilename):
    """Convert line based data output to obj
    FIXME: Obsolete?
    """

    import glob, os
    from anuga.config import data_dir


    #Get bathymetry and x,y's
    lines = open(data_dir+os.sep+basefilename+'_geometry.dat', 'r').readlines()

    from Numeric import zeros, Float

    M = len(lines)  #Number of lines
    x = zeros((M,3), Float)
    y = zeros((M,3), Float)
    z = zeros((M,3), Float)

    ##i = 0
    for i, line in enumerate(lines):
        tokens = line.split()
        values = map(float,tokens)

        for j in range(3):
            x[i,j] = values[j*3]
            y[i,j] = values[j*3+1]
            z[i,j] = values[j*3+2]

        ##i += 1


    #Write obj for bathymetry
    write_obj(data_dir+os.sep+basefilename+'_geometry',x,y,z)


    #Now read all the data files with variable information, combine with
    #x,y info
    #and store as obj

    files = glob.glob(data_dir+os.sep+basefilename+'*.dat')

    for filename in files:
        print 'Processing %s' % filename

        lines = open(data_dir+os.sep+filename,'r').readlines()
        assert len(lines) == M
        root, ext = os.path.splitext(filename)

        #Get time from filename
        i0 = filename.find('_time=')
        if i0 == -1:
            #Skip bathymetry file
            continue

        i0 += 6  #Position where time starts
        i1 = filename.find('.dat')

        if i1 > i0:
            t = float(filename[i0:i1])
        else:
            raise DataTimeError, 'Hmmmm'



        ##i = 0
        for i, line in enumerate(lines):
            tokens = line.split()
            values = map(float,tokens)

            for j in range(3):
                z[i,j] = values[j]

            ##i += 1

        #Write obj for variable data
        write_obj(data_dir+os.sep+basefilename+'_time=%.4f' %t,x,y,z)


def filter_netcdf(filename1, filename2, first=0, last=None, step = 1):
    """Read netcdf filename1, pick timesteps first:step:last and save to
    nettcdf file filename2
    """
    from Scientific.IO.NetCDF import NetCDFFile

    #Get NetCDF
    infile = NetCDFFile(filename1, 'r')  #Open existing file for read
    outfile = NetCDFFile(filename2, 'w')  #Open new file


    #Copy dimensions
    for d in infile.dimensions:
        outfile.createDimension(d, infile.dimensions[d])

    for name in infile.variables:
        var = infile.variables[name]
        outfile.createVariable(name, var.typecode(), var.dimensions)


    #Copy the static variables
    for name in infile.variables:
        if name == 'time' or name == 'stage':
            pass
        else:
            #Copy
            outfile.variables[name][:] = infile.variables[name][:]

    #Copy selected timesteps
    time = infile.variables['time']
    stage = infile.variables['stage']

    newtime = outfile.variables['time']
    newstage = outfile.variables['stage']

    if last is None:
        last = len(time)

    selection = range(first, last, step)
    for i, j in enumerate(selection):
        print 'Copying timestep %d of %d (%f)' %(j, last-first, time[j])
        newtime[i] = time[j]
        newstage[i,:] = stage[j,:]

    #Close
    infile.close()
    outfile.close()


#Get data objects
def get_dataobject(domain, mode='w'):
    """Return instance of class of given format using filename
    """

    cls = eval('Data_format_%s' %domain.format)
    return cls(domain, mode)




def dem2pts(basename_in, basename_out=None,
            easting_min=None, easting_max=None,
            northing_min=None, northing_max=None,
            use_cache=False, verbose=False,):
    """Read Digitial Elevation model from the following NetCDF format (.dem)

    Example:

    ncols         3121
    nrows         1800
    xllcorner     722000
    yllcorner     5893000
    cellsize      25
    NODATA_value  -9999
    138.3698 137.4194 136.5062 135.5558 ..........

    Convert to NetCDF pts format which is

    points:  (Nx2) Float array
    elevation: N Float array
    """



    kwargs = {'basename_out': basename_out,
              'easting_min': easting_min,
              'easting_max': easting_max,
              'northing_min': northing_min,
              'northing_max': northing_max,
              'verbose': verbose}

    if use_cache is True:
        from caching import cache
        result = cache(_dem2pts, basename_in, kwargs,
                       dependencies = [basename_in + '.dem'],
                       verbose = verbose)

    else:
        result = apply(_dem2pts, [basename_in], kwargs)

    return result

def _dem2pts(basename_in, basename_out=None, verbose=False,
            easting_min=None, easting_max=None,
            northing_min=None, northing_max=None):
    """Read Digitial Elevation model from the following NetCDF format (.dem)

    Internal function. See public function dem2pts for details.
    """

    # FIXME: Can this be written feasibly using write_pts?

    import os
    from Scientific.IO.NetCDF import NetCDFFile
    from Numeric import Float, zeros, reshape, sum

    root = basename_in

    # Get NetCDF
    infile = NetCDFFile(root + '.dem', 'r')  # Open existing netcdf file for read

    if verbose: print 'Reading DEM from %s' %(root + '.dem')

    ncols = infile.ncols[0]
    nrows = infile.nrows[0]
    xllcorner = infile.xllcorner[0]  # Easting of lower left corner
    yllcorner = infile.yllcorner[0]  # Northing of lower left corner
    cellsize = infile.cellsize[0]
    NODATA_value = infile.NODATA_value[0]
    dem_elevation = infile.variables['elevation']

    zone = infile.zone[0]
    false_easting = infile.false_easting[0]
    false_northing = infile.false_northing[0]

    # Text strings
    projection = infile.projection
    datum = infile.datum
    units = infile.units


    # Get output file
    if basename_out == None:
        ptsname = root + '.pts'
    else:
        ptsname = basename_out + '.pts'

    if verbose: print 'Store to NetCDF file %s' %ptsname
    # NetCDF file definition
    outfile = NetCDFFile(ptsname, 'w')

    # Create new file
    outfile.institution = 'Geoscience Australia'
    outfile.description = 'NetCDF pts format for compact and portable storage ' +\
                          'of spatial point data'
    # Assign default values
    xllcorner = xllcorner + 0.5*cellsize # Convert to gridline registration
    yllcorner = yllcorner + 0.5*cellsize

    if easting_min is None: easting_min = xllcorner
    if easting_max is None: easting_max = xllcorner + (ncols-1)*cellsize
    if northing_min is None: northing_min = yllcorner
    if northing_max is None: northing_max = yllcorner + (nrows-1)*cellsize

    # Compute offsets to update georeferencing
    easting_offset = xllcorner - easting_min
    northing_offset = yllcorner - northing_min

    # Georeferencing
    outfile.zone = zone
    outfile.xllcorner = easting_min # Easting of lower left corner
    outfile.yllcorner = northing_min # Northing of lower left corner
    outfile.false_easting = false_easting
    outfile.false_northing = false_northing

    outfile.projection = projection
    outfile.datum = datum
    outfile.units = units


    # Grid info (FIXME: probably not going to be used, but heck)
    outfile.ncols = ncols
    outfile.nrows = nrows

    dem_elevation_r = reshape(dem_elevation, (nrows, ncols))
    totalnopoints = nrows*ncols

    # Calculating number of NODATA_values for each row in clipped region
    # FIXME: use array operations to do faster
    nn = 0
    k = 0
    i1_0 = 0
    j1_0 = 0
    thisj = 0
    thisi = 0
    for i in range(nrows):
        y = (nrows-i-1)*cellsize + yllcorner
        for j in range(ncols):
            x = j*cellsize + xllcorner
            if easting_min <= x <= easting_max and \
               northing_min <= y <= northing_max:
                thisj = j
                thisi = i
                if dem_elevation_r[i,j] == NODATA_value: nn += 1

                if k == 0:
                    i1_0 = i
                    j1_0 = j
                k += 1

    index1 = j1_0
    index2 = thisj

    # Dimension definitions
    nrows_in_bounding_box = int(round((northing_max-northing_min)/cellsize))
    ncols_in_bounding_box = int(round((easting_max-easting_min)/cellsize))

    clippednopoints = (thisi+1-i1_0)*(thisj+1-j1_0)
    nopoints = clippednopoints-nn

    clipped_dem_elev = dem_elevation_r[i1_0:thisi+1,j1_0:thisj+1]

    if verbose:
        print 'There are %d values in the elevation' %totalnopoints
        print 'There are %d values in the clipped elevation' %clippednopoints
        print 'There are %d NODATA_values in the clipped elevation' %nn

    outfile.createDimension('number_of_points', nopoints)
    outfile.createDimension('number_of_dimensions', 2) #This is 2d data

    # Variable definitions
    outfile.createVariable('points', Float, ('number_of_points',
                                             'number_of_dimensions'))
    outfile.createVariable('elevation', Float, ('number_of_points',))

    # Get handles to the variables
    points = outfile.variables['points']
    elevation = outfile.variables['elevation']

    lenv = index2-index1+1
    # Store data
    global_index = 0
    # for i in range(nrows):
    for i in range(i1_0,thisi+1,1):
        if verbose and i%((nrows+10)/10)==0:
            print 'Processing row %d of %d' %(i, nrows)

        lower_index = global_index

        v = dem_elevation_r[i,index1:index2+1]
        no_NODATA = sum(v == NODATA_value)
        if no_NODATA > 0:
            newcols = lenv - no_NODATA # ncols_in_bounding_box - no_NODATA
        else:
            newcols = lenv # ncols_in_bounding_box

        telev = zeros(newcols, Float)
        tpoints = zeros((newcols, 2), Float)

        local_index = 0

        y = (nrows-i-1)*cellsize + yllcorner
        #for j in range(ncols):
        for j in range(j1_0,index2+1,1):

            x = j*cellsize + xllcorner
            if easting_min <= x <= easting_max and \
               northing_min <= y <= northing_max and \
               dem_elevation_r[i,j] <> NODATA_value:
                tpoints[local_index, :] = [x-easting_min,y-northing_min]
                telev[local_index] = dem_elevation_r[i, j]
                global_index += 1
                local_index += 1

        upper_index = global_index

        if upper_index == lower_index + newcols:
            points[lower_index:upper_index, :] = tpoints
            elevation[lower_index:upper_index] = telev

    assert global_index == nopoints, 'index not equal to number of points'

    infile.close()
    outfile.close()



def _read_hecras_cross_sections(lines):
    """Return block of surface lines for each cross section
    Starts with SURFACE LINE,
    Ends with END CROSS-SECTION
    """

    points = []

    reading_surface = False
    for i, line in enumerate(lines):

        if len(line.strip()) == 0:    #Ignore blanks
            continue

        if lines[i].strip().startswith('SURFACE LINE'):
            reading_surface = True
            continue

        if lines[i].strip().startswith('END') and reading_surface:
            yield points
            reading_surface = False
            points = []

        if reading_surface:
            fields = line.strip().split(',')
            easting = float(fields[0])
            northing = float(fields[1])
            elevation = float(fields[2])
            points.append([easting, northing, elevation])




def hecras_cross_sections2pts(basename_in,
                              basename_out=None,
                              verbose=False):
    """Read HEC-RAS Elevation datal from the following ASCII format (.sdf)

    Example:


# RAS export file created on Mon 15Aug2005 11:42
# by HEC-RAS Version 3.1.1

BEGIN HEADER:
  UNITS: METRIC
  DTM TYPE: TIN
  DTM: v:\1\cit\perth_topo\river_tin
  STREAM LAYER: c:\local\hecras\21_02_03\up_canning_cent3d.shp
  CROSS-SECTION LAYER: c:\local\hecras\21_02_03\up_can_xs3d.shp
  MAP PROJECTION: UTM
  PROJECTION ZONE: 50
  DATUM: AGD66
  VERTICAL DATUM:
  NUMBER OF REACHES:  19
  NUMBER OF CROSS-SECTIONS:  14206
END HEADER:


Only the SURFACE LINE data of the following form will be utilised

  CROSS-SECTION:
    STREAM ID:Southern-Wungong
    REACH ID:Southern-Wungong
    STATION:19040.*
    CUT LINE:
      405548.671603161 , 6438142.7594925
      405734.536092045 , 6438326.10404912
      405745.130459356 , 6438331.48627354
      405813.89633823 , 6438368.6272789
    SURFACE LINE:
     405548.67,   6438142.76,   35.37
     405552.24,   6438146.28,   35.41
     405554.78,   6438148.78,   35.44
     405555.80,   6438149.79,   35.44
     405559.37,   6438153.31,   35.45
     405560.88,   6438154.81,   35.44
     405562.93,   6438156.83,   35.42
     405566.50,   6438160.35,   35.38
     405566.99,   6438160.83,   35.37
     ...
   END CROSS-SECTION

    Convert to NetCDF pts format which is

    points:  (Nx2) Float array
    elevation: N Float array
    """

    import os
    from Scientific.IO.NetCDF import NetCDFFile
    from Numeric import Float, zeros, reshape

    root = basename_in

    #Get ASCII file
    infile = open(root + '.sdf', 'r')  #Open SDF file for read

    if verbose: print 'Reading DEM from %s' %(root + '.sdf')

    lines = infile.readlines()
    infile.close()

    if verbose: print 'Converting to pts format'

    i = 0
    while lines[i].strip() == '' or lines[i].strip().startswith('#'):
        i += 1

    assert lines[i].strip().upper() == 'BEGIN HEADER:'
    i += 1

    assert lines[i].strip().upper().startswith('UNITS:')
    units = lines[i].strip().split()[1]
    i += 1

    assert lines[i].strip().upper().startswith('DTM TYPE:')
    i += 1

    assert lines[i].strip().upper().startswith('DTM:')
    i += 1

    assert lines[i].strip().upper().startswith('STREAM')
    i += 1

    assert lines[i].strip().upper().startswith('CROSS')
    i += 1

    assert lines[i].strip().upper().startswith('MAP PROJECTION:')
    projection = lines[i].strip().split(':')[1]
    i += 1

    assert lines[i].strip().upper().startswith('PROJECTION ZONE:')
    zone = int(lines[i].strip().split(':')[1])
    i += 1

    assert lines[i].strip().upper().startswith('DATUM:')
    datum = lines[i].strip().split(':')[1]
    i += 1

    assert lines[i].strip().upper().startswith('VERTICAL DATUM:')
    i += 1

    assert lines[i].strip().upper().startswith('NUMBER OF REACHES:')
    i += 1

    assert lines[i].strip().upper().startswith('NUMBER OF CROSS-SECTIONS:')
    number_of_cross_sections = int(lines[i].strip().split(':')[1])
    i += 1


    #Now read all points
    points = []
    elevation = []
    for j, entries in enumerate(_read_hecras_cross_sections(lines[i:])):
        for k, entry in enumerate(entries):
            points.append(entry[:2])
            elevation.append(entry[2])


    msg = 'Actual #number_of_cross_sections == %d, Reported as %d'\
          %(j+1, number_of_cross_sections)
    assert j+1 == number_of_cross_sections, msg

    #Get output file
    if basename_out == None:
        ptsname = root + '.pts'
    else:
        ptsname = basename_out + '.pts'

    geo_ref = Geo_reference(zone, 0, 0, datum, projection, units)
    geo = Geospatial_data(points, {"elevation":elevation},
                          verbose=verbose, geo_reference=geo_ref)
    geo.export_points_file(ptsname)

def export_grid(basename_in, extra_name_out = None,
                quantities = None, # defaults to elevation
                timestep = None,
                reduction = None,
                cellsize = 10,
                NODATA_value = -9999,
                easting_min = None,
                easting_max = None,
                northing_min = None,
                northing_max = None,
                verbose = False,
                origin = None,
                datum = 'WGS84',
                format = 'ers'):
    """
    
    Wrapper for sww2dem. - see sww2dem to find out what most of the
    parameters do.

    Quantities is a list of quantities.  Each quantity will be
    calculated for each sww file.

    This returns the basenames of the files returned, which is made up
    of the dir and all of the file name, except the extension.

    This function returns the names of the files produced.

    It will also produce as many output files as there are input sww files. 
    """
    
    if quantities is None:
        quantities = ['elevation']
        
    if type(quantities) is str:
            quantities = [quantities]

    # How many sww files are there?
    dir, base = os.path.split(basename_in)
    #print "basename_in",basename_in
    #print "base",base

    iterate_over = get_all_swwfiles(dir,base,verbose)
    
    if dir == "":
        dir = "." # Unix compatibility
    
    files_out = []
    #print 'sww_file',iterate_over
    for sww_file in iterate_over:
        for quantity in quantities:
            if extra_name_out is None:
                basename_out = sww_file + '_' + quantity
            else:
                basename_out = sww_file + '_' + quantity + '_' \
                               + extra_name_out
#            print "basename_out", basename_out
        
            file_out = sww2dem(dir+sep+sww_file, dir+sep+basename_out,
                               quantity, 
                               timestep,
                               reduction,
                               cellsize,
                               NODATA_value,
                               easting_min,
                               easting_max,
                               northing_min,
                               northing_max,
                               verbose,
                               origin,
                               datum,
                               format)
            files_out.append(file_out)
    #print "basenames_out after",basenames_out 
    return files_out


def get_timeseries(production_dirs, output_dir, scenario_name, gauges_dir_name,
                   plot_quantity, generate_fig = False,
                   reportname = None, surface = False, time_min = None,
                   time_max = None, title_on = False, verbose = True,
                   nodes=None):
    """
    nodes - number of processes used.

    warning - this function has no tests
    """
    if reportname == None:
        report = False
    else:
        report = True
        
    if nodes is None:
        is_parallel = False
    else:
        is_parallel = True
        
    # Generate figures
    swwfiles = {}
    
    if is_parallel is True:    
        for i in range(nodes):
            print 'Sending node %d of %d' %(i,nodes)
            swwfiles = {}
            if not reportname == None:
                reportname = report_name + '_%s' %(i)
            for label_id in production_dirs.keys():
                if label_id == 'boundaries':
                    swwfile = best_boundary_sww
                else:
                    file_loc = output_dir + label_id + sep
                    sww_extra = '_P%s_%s' %(i,nodes)
                    swwfile = file_loc + scenario_name + sww_extra + '.sww'
                    print 'swwfile',swwfile
                    swwfiles[swwfile] = label_id

                texname, elev_output = sww2timeseries(swwfiles,
                                              gauges_dir_name,
                                              production_dirs,
                                              report = report,
                                              reportname = reportname,
                                              plot_quantity = plot_quantity,
                                              generate_fig = generate_fig,
                                              surface = surface,
                                              time_min = time_min,
                                              time_max = time_max,
                                              title_on = title_on,
                                              verbose = verbose)
    else:   
        for label_id in production_dirs.keys():       
            if label_id == 'boundaries':
                print 'boundaries'
                file_loc = project.boundaries_in_dir
                swwfile = project.boundaries_dir_name3 + '.sww'
                #  swwfile = boundary_dir_filename
            else:
                file_loc = output_dir + label_id + sep
                swwfile = file_loc + scenario_name + '.sww'
            swwfiles[swwfile] = label_id
        
        texname, elev_output = sww2timeseries(swwfiles,
                                              gauges_dir_name,
                                              production_dirs,
                                              report = report,
                                              reportname = reportname,
                                              plot_quantity = plot_quantity,
                                              generate_fig = generate_fig,
                                              surface = surface,
                                              time_min = time_min,
                                              time_max = time_max,
                                              title_on = title_on,
                                              verbose = verbose)
                                          

    
def sww2dem(basename_in, basename_out = None,
            quantity = None, # defaults to elevation
            timestep = None,
            reduction = None,
            cellsize = 10,
            NODATA_value = -9999,
            easting_min = None,
            easting_max = None,
            northing_min = None,
            northing_max = None,
            verbose = False,
            origin = None,
            datum = 'WGS84',
            format = 'ers'):

    """Read SWW file and convert to Digitial Elevation model format
    (.asc or .ers)

    Example (ASC):

    ncols         3121
    nrows         1800
    xllcorner     722000
    yllcorner     5893000
    cellsize      25
    NODATA_value  -9999
    138.3698 137.4194 136.5062 135.5558 ..........

    Also write accompanying file with same basename_in but extension .prj
    used to fix the UTM zone, datum, false northings and eastings.

    The prj format is assumed to be as

    Projection    UTM
    Zone          56
    Datum         WGS84
    Zunits        NO
    Units         METERS
    Spheroid      WGS84
    Xshift        0.0000000000
    Yshift        10000000.0000000000
    Parameters


    The parameter quantity must be the name of an existing quantity or
    an expression involving existing quantities. The default is
    'elevation'. Quantity is not a list of quantities.

    if timestep (an index) is given, output quantity at that timestep

    if reduction is given use that to reduce quantity over all timesteps.

    datum

    format can be either 'asc' or 'ers'
    """

    import sys
    from Numeric import array, Float, concatenate, NewAxis, zeros, reshape, \
         sometrue
    from Numeric import array2string

    from anuga.utilities.polygon import inside_polygon, outside_polygon, \
         separate_points_by_polygon
    from anuga.abstract_2d_finite_volumes.util import \
         apply_expression_to_dictionary

    msg = 'Format must be either asc or ers'
    assert format.lower() in ['asc', 'ers'], msg


    false_easting = 500000
    false_northing = 10000000

    if quantity is None:
        quantity = 'elevation'
        
    if reduction is None:
        reduction = max

    if basename_out is None:
        basename_out = basename_in + '_%s' %quantity

    if quantity_formula.has_key(quantity):
        quantity = quantity_formula[quantity]
        
    swwfile = basename_in + '.sww'
    demfile = basename_out + '.' + format
    # Note the use of a .ers extension is optional (write_ermapper_grid will
    # deal with either option
    
    #if verbose: bye= nsuadsfd[0] # uncomment to check catching verbose errors
    
    # Read sww file
    if verbose: 
        print 'Reading from %s' %swwfile
        print 'Output directory is %s' %basename_out
    
    from Scientific.IO.NetCDF import NetCDFFile
    fid = NetCDFFile(swwfile)

    #Get extent and reference
    x = fid.variables['x'][:]
    y = fid.variables['y'][:]
    volumes = fid.variables['volumes'][:]
    if timestep is not None:
        times = fid.variables['time'][timestep]
    else:
        times = fid.variables['time'][:]

    number_of_timesteps = fid.dimensions['number_of_timesteps']
    number_of_points = fid.dimensions['number_of_points']
    
    if origin is None:

        # Get geo_reference
        # sww files don't have to have a geo_ref
        try:
            geo_reference = Geo_reference(NetCDFObject=fid)
        except AttributeError, e:
            geo_reference = Geo_reference() # Default georef object

        xllcorner = geo_reference.get_xllcorner()
        yllcorner = geo_reference.get_yllcorner()
        zone = geo_reference.get_zone()
    else:
        zone = origin[0]
        xllcorner = origin[1]
        yllcorner = origin[2]



    # FIXME: Refactor using code from Interpolation_function.statistics
    # (in interpolate.py)
    # Something like print swwstats(swwname)
    if verbose:
        print '------------------------------------------------'
        print 'Statistics of SWW file:'
        print '  Name: %s' %swwfile
        print '  Reference:'
        print '    Lower left corner: [%f, %f]'\
              %(xllcorner, yllcorner)
        if timestep is not None:
            print '    Time: %f' %(times)
        else:
            print '    Start time: %f' %fid.starttime[0]
        print '  Extent:'
        print '    x [m] in [%f, %f], len(x) == %d'\
              %(min(x.flat), max(x.flat), len(x.flat))
        print '    y [m] in [%f, %f], len(y) == %d'\
              %(min(y.flat), max(y.flat), len(y.flat))
        if timestep is not None:
            print '    t [s] = %f, len(t) == %d' %(times, 1)
        else:
            print '    t [s] in [%f, %f], len(t) == %d'\
              %(min(times), max(times), len(times))
        print '  Quantities [SI units]:'
        for name in ['stage', 'xmomentum', 'ymomentum']:
            q = fid.variables[name][:].flat
            if timestep is not None:
                q = q[timestep*len(x):(timestep+1)*len(x)]
            if verbose: print '    %s in [%f, %f]' %(name, min(q), max(q))
        for name in ['elevation']:
            q = fid.variables[name][:].flat
            if verbose: print '    %s in [%f, %f]' %(name, min(q), max(q))
            
    # Get quantity and reduce if applicable
    if verbose: print 'Processing quantity %s' %quantity

    # Turn NetCDF objects into Numeric arrays
    quantity_dict = {}
    for name in fid.variables.keys():
        quantity_dict[name] = fid.variables[name][:] 


    # Convert quantity expression to quantities found in sww file    
    q = apply_expression_to_dictionary(quantity, quantity_dict)

    if len(q.shape) == 2:
        #q has a time component and needs to be reduced along
        #the temporal dimension
        if verbose: print 'Reducing quantity %s' %quantity
        q_reduced = zeros( number_of_points, Float )
        
        if timestep is not None:
            for k in range(number_of_points):
                q_reduced[k] = q[timestep,k] 
        else:
            for k in range(number_of_points):
                q_reduced[k] = reduction( q[:,k] )

        q = q_reduced

    #Post condition: Now q has dimension: number_of_points
    assert len(q.shape) == 1
    assert q.shape[0] == number_of_points


    if verbose:
        print 'Processed values for %s are in [%f, %f]' %(quantity, min(q), max(q))


    #Create grid and update xll/yll corner and x,y

    #Relative extent
    if easting_min is None:
        xmin = min(x)
    else:
        xmin = easting_min - xllcorner

    if easting_max is None:
        xmax = max(x)
    else:
        xmax = easting_max - xllcorner

    if northing_min is None:
        ymin = min(y)
    else:
        ymin = northing_min - yllcorner

    if northing_max is None:
        ymax = max(y)
    else:
        ymax = northing_max - yllcorner



    if verbose: print 'Creating grid'
    ncols = int((xmax-xmin)/cellsize)+1
    nrows = int((ymax-ymin)/cellsize)+1


    #New absolute reference and coordinates
    newxllcorner = xmin+xllcorner
    newyllcorner = ymin+yllcorner

    x = x+xllcorner-newxllcorner
    y = y+yllcorner-newyllcorner
    
    vertex_points = concatenate ((x[:, NewAxis] ,y[:, NewAxis]), axis = 1)
    assert len(vertex_points.shape) == 2

    grid_points = zeros ( (ncols*nrows, 2), Float )


    for i in xrange(nrows):
        if format.lower() == 'asc':
            yg = i*cellsize
        else:
        #this will flip the order of the y values for ers
            yg = (nrows-i)*cellsize

        for j in xrange(ncols):
            xg = j*cellsize
            k = i*ncols + j

            grid_points[k,0] = xg
            grid_points[k,1] = yg

    #Interpolate
    from anuga.fit_interpolate.interpolate import Interpolate

    # Remove loners from vertex_points, volumes here
    vertex_points, volumes = remove_lone_verts(vertex_points, volumes)
    #export_mesh_file('monkey.tsh',{'vertices':vertex_points, 'triangles':volumes})
    #import sys; sys.exit()
    interp = Interpolate(vertex_points, volumes, verbose = verbose)

    #Interpolate using quantity values
    if verbose: print 'Interpolating'
    grid_values = interp.interpolate(q, grid_points).flat


    if verbose:
        print 'Interpolated values are in [%f, %f]' %(min(grid_values),
                                                      max(grid_values))

    #Assign NODATA_value to all points outside bounding polygon (from interpolation mesh)
    P = interp.mesh.get_boundary_polygon()
    outside_indices = outside_polygon(grid_points, P, closed=True)

    for i in outside_indices:
        grid_values[i] = NODATA_value




    if format.lower() == 'ers':
        # setup ERS header information
        grid_values = reshape(grid_values,(nrows, ncols))
        header = {}
        header['datum'] = '"' + datum + '"'
        # FIXME The use of hardwired UTM and zone number needs to be made optional
        # FIXME Also need an automatic test for coordinate type (i.e. EN or LL)
        header['projection'] = '"UTM-' + str(zone) + '"'
        header['coordinatetype'] = 'EN'
        if header['coordinatetype'] == 'LL':
            header['longitude'] = str(newxllcorner)
            header['latitude'] = str(newyllcorner)
        elif header['coordinatetype'] == 'EN':
            header['eastings'] = str(newxllcorner)
            header['northings'] = str(newyllcorner)
        header['nullcellvalue'] = str(NODATA_value)
        header['xdimension'] = str(cellsize)
        header['ydimension'] = str(cellsize)
        header['value'] = '"' + quantity + '"'
        #header['celltype'] = 'IEEE8ByteReal'  #FIXME: Breaks unit test


        #Write
        if verbose: print 'Writing %s' %demfile
        import ermapper_grids
        ermapper_grids.write_ermapper_grid(demfile, grid_values, header)

        fid.close()
    else:
        #Write to Ascii format

        #Write prj file
        prjfile = basename_out + '.prj'

        if verbose: print 'Writing %s' %prjfile
        prjid = open(prjfile, 'w')
        prjid.write('Projection    %s\n' %'UTM')
        prjid.write('Zone          %d\n' %zone)
        prjid.write('Datum         %s\n' %datum)
        prjid.write('Zunits        NO\n')
        prjid.write('Units         METERS\n')
        prjid.write('Spheroid      %s\n' %datum)
        prjid.write('Xshift        %d\n' %false_easting)
        prjid.write('Yshift        %d\n' %false_northing)
        prjid.write('Parameters\n')
        prjid.close()



        if verbose: print 'Writing %s' %demfile

        ascid = open(demfile, 'w')

        ascid.write('ncols         %d\n' %ncols)
        ascid.write('nrows         %d\n' %nrows)
        ascid.write('xllcorner     %d\n' %newxllcorner)
        ascid.write('yllcorner     %d\n' %newyllcorner)
        ascid.write('cellsize      %f\n' %cellsize)
        ascid.write('NODATA_value  %d\n' %NODATA_value)


        #Get bounding polygon from mesh
        #P = interp.mesh.get_boundary_polygon()
        #inside_indices = inside_polygon(grid_points, P)

        for i in range(nrows):
            if verbose and i%((nrows+10)/10)==0:
                print 'Doing row %d of %d' %(i, nrows)

            base_index = (nrows-i-1)*ncols

            slice = grid_values[base_index:base_index+ncols]
            s = array2string(slice, max_line_width=sys.maxint)
            ascid.write(s[1:-1] + '\n')


            #print
            #for j in range(ncols):
            #    index = base_index+j#
            #    print grid_values[index],
            #    ascid.write('%f ' %grid_values[index])
            #ascid.write('\n')

        #Close
        ascid.close()
        fid.close()
        return basename_out

#Backwards compatibility
def sww2asc(basename_in, basename_out = None,
            quantity = None,
            timestep = None,
            reduction = None,
            cellsize = 10,
            verbose = False,
            origin = None):
    print 'sww2asc will soon be obsoleted - please use sww2dem'
    sww2dem(basename_in,
            basename_out = basename_out,
            quantity = quantity,
            timestep = timestep,
            reduction = reduction,
            cellsize = cellsize,
            verbose = verbose,
            origin = origin,
        datum = 'WGS84',
        format = 'asc')

def sww2ers(basename_in, basename_out = None,
            quantity = None,
            timestep = None,
            reduction = None,
            cellsize = 10,
            verbose = False,
            origin = None,
            datum = 'WGS84'):
    print 'sww2ers will soon be obsoleted - please use sww2dem'
    sww2dem(basename_in,
            basename_out = basename_out,
            quantity = quantity,
            timestep = timestep,
            reduction = reduction,
            cellsize = cellsize,
            verbose = verbose,
            origin = origin,
            datum = datum,
            format = 'ers')
################################# END COMPATIBILITY ##############



def sww2pts(basename_in, basename_out=None,
            data_points=None,
            quantity=None,
            timestep=None,
            reduction=None,
            NODATA_value=-9999,
            verbose=False,
            origin=None):
            #datum = 'WGS84')


    """Read SWW file and convert to interpolated values at selected points

    The parameter quantity' must be the name of an existing quantity or
    an expression involving existing quantities. The default is
    'elevation'.

    if timestep (an index) is given, output quantity at that timestep

    if reduction is given use that to reduce quantity over all timesteps.

    data_points (Nx2 array) give locations of points where quantity is to be computed.
    
    """

    import sys
    from Numeric import array, Float, concatenate, NewAxis, zeros, reshape, sometrue
    from Numeric import array2string

    from anuga.utilities.polygon import inside_polygon, outside_polygon, separate_points_by_polygon
    from anuga.abstract_2d_finite_volumes.util import apply_expression_to_dictionary

    from anuga.geospatial_data.geospatial_data import Geospatial_data

    if quantity is None:
        quantity = 'elevation'

    if reduction is None:
        reduction = max

    if basename_out is None:
        basename_out = basename_in + '_%s' %quantity

    swwfile = basename_in + '.sww'
    ptsfile = basename_out + '.pts'

    # Read sww file
    if verbose: print 'Reading from %s' %swwfile
    from Scientific.IO.NetCDF import NetCDFFile
    fid = NetCDFFile(swwfile)

    # Get extent and reference
    x = fid.variables['x'][:]
    y = fid.variables['y'][:]
    volumes = fid.variables['volumes'][:]

    number_of_timesteps = fid.dimensions['number_of_timesteps']
    number_of_points = fid.dimensions['number_of_points']
    if origin is None:

        # Get geo_reference
        # sww files don't have to have a geo_ref
        try:
            geo_reference = Geo_reference(NetCDFObject=fid)
        except AttributeError, e:
            geo_reference = Geo_reference() #Default georef object

        xllcorner = geo_reference.get_xllcorner()
        yllcorner = geo_reference.get_yllcorner()
        zone = geo_reference.get_zone()
    else:
        zone = origin[0]
        xllcorner = origin[1]
        yllcorner = origin[2]



    # FIXME: Refactor using code from file_function.statistics
    # Something like print swwstats(swwname)
    if verbose:
        x = fid.variables['x'][:]
        y = fid.variables['y'][:]
        times = fid.variables['time'][:]
        print '------------------------------------------------'
        print 'Statistics of SWW file:'
        print '  Name: %s' %swwfile
        print '  Reference:'
        print '    Lower left corner: [%f, %f]'\
              %(xllcorner, yllcorner)
        print '    Start time: %f' %fid.starttime[0]
        print '  Extent:'
        print '    x [m] in [%f, %f], len(x) == %d'\
              %(min(x.flat), max(x.flat), len(x.flat))
        print '    y [m] in [%f, %f], len(y) == %d'\
              %(min(y.flat), max(y.flat), len(y.flat))
        print '    t [s] in [%f, %f], len(t) == %d'\
              %(min(times), max(times), len(times))
        print '  Quantities [SI units]:'
        for name in ['stage', 'xmomentum', 'ymomentum', 'elevation']:
            q = fid.variables[name][:].flat
            print '    %s in [%f, %f]' %(name, min(q), max(q))



    # Get quantity and reduce if applicable
    if verbose: print 'Processing quantity %s' %quantity

    # Turn NetCDF objects into Numeric arrays
    quantity_dict = {}
    for name in fid.variables.keys():
        quantity_dict[name] = fid.variables[name][:]



    # Convert quantity expression to quantities found in sww file    
    q = apply_expression_to_dictionary(quantity, quantity_dict)



    if len(q.shape) == 2:
        # q has a time component and needs to be reduced along
        # the temporal dimension
        if verbose: print 'Reducing quantity %s' %quantity
        q_reduced = zeros( number_of_points, Float )

        for k in range(number_of_points):
            q_reduced[k] = reduction( q[:,k] )

        q = q_reduced

    # Post condition: Now q has dimension: number_of_points
    assert len(q.shape) == 1
    assert q.shape[0] == number_of_points


    if verbose:
        print 'Processed values for %s are in [%f, %f]' %(quantity, min(q), max(q))


    # Create grid and update xll/yll corner and x,y
    vertex_points = concatenate ((x[:, NewAxis] ,y[:, NewAxis]), axis = 1)
    assert len(vertex_points.shape) == 2

    # Interpolate
    from anuga.fit_interpolate.interpolate import Interpolate
    interp = Interpolate(vertex_points, volumes, verbose = verbose)

    # Interpolate using quantity values
    if verbose: print 'Interpolating'
    interpolated_values = interp.interpolate(q, data_points).flat


    if verbose:
        print 'Interpolated values are in [%f, %f]' %(min(interpolated_values),
                                                      max(interpolated_values))

    # Assign NODATA_value to all points outside bounding polygon (from interpolation mesh)
    P = interp.mesh.get_boundary_polygon()
    outside_indices = outside_polygon(data_points, P, closed=True)

    for i in outside_indices:
        interpolated_values[i] = NODATA_value


    # Store results    
    G = Geospatial_data(data_points=data_points,
                        attributes=interpolated_values)

    G.export_points_file(ptsfile, absolute = True)

    fid.close()


def convert_dem_from_ascii2netcdf(basename_in, basename_out = None,
                                  use_cache = False,
                                  verbose = False):
    """Read Digitial Elevation model from the following ASCII format (.asc)

    Example:

    ncols         3121
    nrows         1800
    xllcorner     722000
    yllcorner     5893000
    cellsize      25
    NODATA_value  -9999
    138.3698 137.4194 136.5062 135.5558 ..........

    Convert basename_in + '.asc' to NetCDF format (.dem)
    mimicking the ASCII format closely.


    An accompanying file with same basename_in but extension .prj must exist
    and is used to fix the UTM zone, datum, false northings and eastings.

    The prj format is assumed to be as

    Projection    UTM
    Zone          56
    Datum         WGS84
    Zunits        NO
    Units         METERS
    Spheroid      WGS84
    Xshift        0.0000000000
    Yshift        10000000.0000000000
    Parameters
    """



    kwargs = {'basename_out': basename_out, 'verbose': verbose}

    if use_cache is True:
        from caching import cache
        result = cache(_convert_dem_from_ascii2netcdf, basename_in, kwargs,
                       dependencies = [basename_in + '.asc',
                                       basename_in + '.prj'],
                       verbose = verbose)

    else:
        result = apply(_convert_dem_from_ascii2netcdf, [basename_in], kwargs)

    return result






def _convert_dem_from_ascii2netcdf(basename_in, basename_out = None,
                                  verbose = False):
    """Read Digitial Elevation model from the following ASCII format (.asc)

    Internal function. See public function convert_dem_from_ascii2netcdf for details.
    """

    import os
    from Scientific.IO.NetCDF import NetCDFFile
    from Numeric import Float, array

    #root, ext = os.path.splitext(basename_in)
    root = basename_in

    ###########################################
    # Read Meta data
    if verbose: print 'Reading METADATA from %s' %root + '.prj'
    metadatafile = open(root + '.prj')
    metalines = metadatafile.readlines()
    metadatafile.close()

    L = metalines[0].strip().split()
    assert L[0].strip().lower() == 'projection'
    projection = L[1].strip()                   #TEXT

    L = metalines[1].strip().split()
    assert L[0].strip().lower() == 'zone'
    zone = int(L[1].strip())

    L = metalines[2].strip().split()
    assert L[0].strip().lower() == 'datum'
    datum = L[1].strip()                        #TEXT

    L = metalines[3].strip().split()
    assert L[0].strip().lower() == 'zunits'     #IGNORE
    zunits = L[1].strip()                       #TEXT

    L = metalines[4].strip().split()
    assert L[0].strip().lower() == 'units'
    units = L[1].strip()                        #TEXT

    L = metalines[5].strip().split()
    assert L[0].strip().lower() == 'spheroid'   #IGNORE
    spheroid = L[1].strip()                     #TEXT

    L = metalines[6].strip().split()
    assert L[0].strip().lower() == 'xshift'
    false_easting = float(L[1].strip())

    L = metalines[7].strip().split()
    assert L[0].strip().lower() == 'yshift'
    false_northing = float(L[1].strip())

    #print false_easting, false_northing, zone, datum


    ###########################################
    #Read DEM data

    datafile = open(basename_in + '.asc')

    if verbose: print 'Reading DEM from %s' %(basename_in + '.asc')
    lines = datafile.readlines()
    datafile.close()

    if verbose: print 'Got', len(lines), ' lines'

    ncols = int(lines[0].split()[1].strip())
    nrows = int(lines[1].split()[1].strip())
    xllcorner = float(lines[2].split()[1].strip())
    yllcorner = float(lines[3].split()[1].strip())
    cellsize = float(lines[4].split()[1].strip())
    NODATA_value = int(lines[5].split()[1].strip())

    assert len(lines) == nrows + 6


    ##########################################


    if basename_out == None:
        netcdfname = root + '.dem'
    else:
        netcdfname = basename_out + '.dem'

    if verbose: print 'Store to NetCDF file %s' %netcdfname
    # NetCDF file definition
    fid = NetCDFFile(netcdfname, 'w')

    #Create new file
    fid.institution = 'Geoscience Australia'
    fid.description = 'NetCDF DEM format for compact and portable storage ' +\
                      'of spatial point data'

    fid.ncols = ncols
    fid.nrows = nrows
    fid.xllcorner = xllcorner
    fid.yllcorner = yllcorner
    fid.cellsize = cellsize
    fid.NODATA_value = NODATA_value

    fid.zone = zone
    fid.false_easting = false_easting
    fid.false_northing = false_northing
    fid.projection = projection
    fid.datum = datum
    fid.units = units


    # dimension definitions
    fid.createDimension('number_of_rows', nrows)
    fid.createDimension('number_of_columns', ncols)

    # variable definitions
    fid.createVariable('elevation', Float, ('number_of_rows',
                                            'number_of_columns'))

    # Get handles to the variables
    elevation = fid.variables['elevation']

    #Store data
    n = len(lines[6:])
    for i, line in enumerate(lines[6:]):
        fields = line.split()
        if verbose and i%((n+10)/10)==0:
            print 'Processing row %d of %d' %(i, nrows)

        elevation[i, :] = array([float(x) for x in fields])

    fid.close()





def ferret2sww(basename_in, basename_out = None,
               verbose = False,
               minlat = None, maxlat = None,
               minlon = None, maxlon = None,
               mint = None, maxt = None, mean_stage = 0,
               origin = None, zscale = 1,
               fail_on_NaN = True,
               NaN_filler = 0,
               elevation = None,
               inverted_bathymetry = True
               ): #FIXME: Bathymetry should be obtained
                                  #from MOST somehow.
                                  #Alternatively from elsewhere
                                  #or, as a last resort,
                                  #specified here.
                                  #The value of -100 will work
                                  #for the Wollongong tsunami
                                  #scenario but is very hacky
    """Convert MOST and 'Ferret' NetCDF format for wave propagation to
    sww format native to abstract_2d_finite_volumes.

    Specify only basename_in and read files of the form
    basefilename_ha.nc, basefilename_ua.nc, basefilename_va.nc containing
    relative height, x-velocity and y-velocity, respectively.

    Also convert latitude and longitude to UTM. All coordinates are
    assumed to be given in the GDA94 datum.

    min's and max's: If omitted - full extend is used.
    To include a value min may equal it, while max must exceed it.
    Lat and lon are assuemd to be in decimal degrees

    origin is a 3-tuple with geo referenced
    UTM coordinates (zone, easting, northing)

    nc format has values organised as HA[TIME, LATITUDE, LONGITUDE]
    which means that longitude is the fastest
    varying dimension (row major order, so to speak)

    ferret2sww uses grid points as vertices in a triangular grid
    counting vertices from lower left corner upwards, then right
    """

    import os
    from Scientific.IO.NetCDF import NetCDFFile
    from Numeric import Float, Int, Int32, searchsorted, zeros, array
    from Numeric import allclose, around

    precision = Float

    msg = 'Must use latitudes and longitudes for minlat, maxlon etc'

    if minlat != None:
        assert -90 < minlat < 90 , msg
    if maxlat != None:
        assert -90 < maxlat < 90 , msg
        if minlat != None:
            assert maxlat > minlat
    if minlon != None:
        assert -180 < minlon < 180 , msg
    if maxlon != None:
        assert -180 < maxlon < 180 , msg
        if minlon != None:
            assert maxlon > minlon
        


    #Get NetCDF data
    if verbose: print 'Reading files %s_*.nc' %basename_in
    #print "basename_in + '_ha.nc'",basename_in + '_ha.nc' 
    file_h = NetCDFFile(basename_in + '_ha.nc', 'r') #Wave amplitude (cm)
    file_u = NetCDFFile(basename_in + '_ua.nc', 'r') #Velocity (x) (cm/s)
    file_v = NetCDFFile(basename_in + '_va.nc', 'r') #Velocity (y) (cm/s)
    file_e = NetCDFFile(basename_in + '_e.nc', 'r')  #Elevation (z) (m)

    if basename_out is None:
        swwname = basename_in + '.sww'
    else:
        swwname = basename_out + '.sww'

    # Get dimensions of file_h
    for dimension in file_h.dimensions.keys():
        if dimension[:3] == 'LON':
            dim_h_longitude = dimension
        if dimension[:3] == 'LAT':
            dim_h_latitude = dimension
        if dimension[:4] == 'TIME':
            dim_h_time = dimension

#    print 'long:', dim_h_longitude
#    print 'lats:', dim_h_latitude
#    print 'times:', dim_h_time

    times = file_h.variables[dim_h_time]
    latitudes = file_h.variables[dim_h_latitude]
    longitudes = file_h.variables[dim_h_longitude]
    
    # get dimensions for file_e
    for dimension in file_e.dimensions.keys():
        if dimension[:3] == 'LON':
            dim_e_longitude = dimension
        if dimension[:3] == 'LAT':
            dim_e_latitude = dimension

    # get dimensions for file_u
    for dimension in file_u.dimensions.keys():
        if dimension[:3] == 'LON':
            dim_u_longitude = dimension
        if dimension[:3] == 'LAT':
            dim_u_latitude = dimension
        if dimension[:4] == 'TIME':
            dim_u_time = dimension
            
    # get dimensions for file_v
    for dimension in file_v.dimensions.keys():
        if dimension[:3] == 'LON':
            dim_v_longitude = dimension
        if dimension[:3] == 'LAT':
            dim_v_latitude = dimension
        if dimension[:4] == 'TIME':
            dim_v_time = dimension


    # Precision used by most for lat/lon is 4 or 5 decimals
    e_lat = around(file_e.variables[dim_e_latitude][:], 5)
    e_lon = around(file_e.variables[dim_e_longitude][:], 5)

    # Check that files are compatible
    assert allclose(latitudes, file_u.variables[dim_u_latitude])
    assert allclose(latitudes, file_v.variables[dim_v_latitude])
    assert allclose(latitudes, e_lat)

    assert allclose(longitudes, file_u.variables[dim_u_longitude])
    assert allclose(longitudes, file_v.variables[dim_v_longitude])
    assert allclose(longitudes, e_lon)

    if mint is None:
        jmin = 0
        mint = times[0]        
    else:
        jmin = searchsorted(times, mint)
        
    if maxt is None:
        jmax = len(times)
        maxt = times[-1]
    else:
        jmax = searchsorted(times, maxt)

    #print "latitudes[:]",latitudes[:]
    #print "longitudes[:]",longitudes [:]
    kmin, kmax, lmin, lmax = _get_min_max_indexes(latitudes[:],
                                                  longitudes[:],
                                                  minlat, maxlat,
                                                  minlon, maxlon)


    times = times[jmin:jmax]
    latitudes = latitudes[kmin:kmax]
    longitudes = longitudes[lmin:lmax]

    #print "latitudes[:]",latitudes[:]
    #print "longitudes[:]",longitudes [:]

    if verbose: print 'cropping'
    zname = 'ELEVATION'

    amplitudes = file_h.variables['HA'][jmin:jmax, kmin:kmax, lmin:lmax]
    uspeed = file_u.variables['UA'][jmin:jmax, kmin:kmax, lmin:lmax] #Lon
    vspeed = file_v.variables['VA'][jmin:jmax, kmin:kmax, lmin:lmax] #Lat
    elevations = file_e.variables[zname][kmin:kmax, lmin:lmax]

    #    if latitudes2[0]==latitudes[0] and latitudes2[-1]==latitudes[-1]:
    #        elevations = file_e.variables['ELEVATION'][kmin:kmax, lmin:lmax]
    #    elif latitudes2[0]==latitudes[-1] and latitudes2[-1]==latitudes[0]:
    #        from Numeric import asarray
    #        elevations=elevations.tolist()
    #        elevations.reverse()
    #        elevations=asarray(elevations)
    #    else:
    #        from Numeric import asarray
    #        elevations=elevations.tolist()
    #        elevations.reverse()
    #        elevations=asarray(elevations)
    #        'print hmmm'



    #Get missing values
    nan_ha = file_h.variables['HA'].missing_value[0]
    nan_ua = file_u.variables['UA'].missing_value[0]
    nan_va = file_v.variables['VA'].missing_value[0]
    if hasattr(file_e.variables[zname],'missing_value'):
        nan_e  = file_e.variables[zname].missing_value[0]
    else:
        nan_e = None

    #Cleanup
    from Numeric import sometrue

    missing = (amplitudes == nan_ha)
    if sometrue (missing):
        if fail_on_NaN:
            msg = 'NetCDFFile %s contains missing values'\
                  %(basename_in+'_ha.nc')
            raise DataMissingValuesError, msg
        else:
            amplitudes = amplitudes*(missing==0) + missing*NaN_filler

    missing = (uspeed == nan_ua)
    if sometrue (missing):
        if fail_on_NaN:
            msg = 'NetCDFFile %s contains missing values'\
                  %(basename_in+'_ua.nc')
            raise DataMissingValuesError, msg
        else:
            uspeed = uspeed*(missing==0) + missing*NaN_filler

    missing = (vspeed == nan_va)
    if sometrue (missing):
        if fail_on_NaN:
            msg = 'NetCDFFile %s contains missing values'\
                  %(basename_in+'_va.nc')
            raise DataMissingValuesError, msg
        else:
            vspeed = vspeed*(missing==0) + missing*NaN_filler


    missing = (elevations == nan_e)
    if sometrue (missing):
        if fail_on_NaN:
            msg = 'NetCDFFile %s contains missing values'\
                  %(basename_in+'_e.nc')
            raise DataMissingValuesError, msg
        else:
            elevations = elevations*(missing==0) + missing*NaN_filler

    #######



    number_of_times = times.shape[0]
    number_of_latitudes = latitudes.shape[0]
    number_of_longitudes = longitudes.shape[0]

    assert amplitudes.shape[0] == number_of_times
    assert amplitudes.shape[1] == number_of_latitudes
    assert amplitudes.shape[2] == number_of_longitudes

    if verbose:
        print '------------------------------------------------'
        print 'Statistics:'
        print '  Extent (lat/lon):'
        print '    lat in [%f, %f], len(lat) == %d'\
              %(min(latitudes.flat), max(latitudes.flat),
                len(latitudes.flat))
        print '    lon in [%f, %f], len(lon) == %d'\
              %(min(longitudes.flat), max(longitudes.flat),
                len(longitudes.flat))
        print '    t in [%f, %f], len(t) == %d'\
              %(min(times.flat), max(times.flat), len(times.flat))

        q = amplitudes.flat
        name = 'Amplitudes (ha) [cm]'
        print '  %s in [%f, %f]' %(name, min(q), max(q))

        q = uspeed.flat
        name = 'Speeds (ua) [cm/s]'
        print '  %s in [%f, %f]' %(name, min(q), max(q))

        q = vspeed.flat
        name = 'Speeds (va) [cm/s]'
        print '  %s in [%f, %f]' %(name, min(q), max(q))

        q = elevations.flat
        name = 'Elevations (e) [m]'
        print '  %s in [%f, %f]' %(name, min(q), max(q))


    # print number_of_latitudes, number_of_longitudes
    number_of_points = number_of_latitudes*number_of_longitudes
    number_of_volumes = (number_of_latitudes-1)*(number_of_longitudes-1)*2


    file_h.close()
    file_u.close()
    file_v.close()
    file_e.close()


    # NetCDF file definition
    outfile = NetCDFFile(swwname, 'w')

    description = 'Converted from Ferret files: %s, %s, %s, %s'\
                  %(basename_in + '_ha.nc',
                    basename_in + '_ua.nc',
                    basename_in + '_va.nc',
                    basename_in + '_e.nc')
    
    # Create new file
    starttime = times[0]
    sww = Write_sww()
    sww.store_header(outfile, times, number_of_volumes,
                     number_of_points, description=description,
                     verbose=verbose)

    # Store
    from anuga.coordinate_transforms.redfearn import redfearn
    x = zeros(number_of_points, Float)  #Easting
    y = zeros(number_of_points, Float)  #Northing


    if verbose: print 'Making triangular grid'

    # Check zone boundaries
    refzone, _, _ = redfearn(latitudes[0],longitudes[0])

    vertices = {}
    i = 0
    for k, lat in enumerate(latitudes):       #Y direction
        for l, lon in enumerate(longitudes):  #X direction

            vertices[l,k] = i

            zone, easting, northing = redfearn(lat,lon)

            msg = 'Zone boundary crossed at longitude =', lon
            #assert zone == refzone, msg
            #print '%7.2f %7.2f %8.2f %8.2f' %(lon, lat, easting, northing)
            x[i] = easting
            y[i] = northing
            i += 1

    #Construct 2 triangles per 'rectangular' element
    volumes = []
    for l in range(number_of_longitudes-1):    #X direction
        for k in range(number_of_latitudes-1): #Y direction
            v1 = vertices[l,k+1]
            v2 = vertices[l,k]
            v3 = vertices[l+1,k+1]
            v4 = vertices[l+1,k]

            volumes.append([v1,v2,v3]) #Upper element
            volumes.append([v4,v3,v2]) #Lower element

    volumes = array(volumes)

    if origin is None:
        origin = Geo_reference(refzone,min(x),min(y))
    geo_ref = write_NetCDF_georeference(origin, outfile)
    
    if elevation is not None:
        z = elevation
    else:
        if inverted_bathymetry:
            z = -1*elevations
        else:
            z = elevations
    #FIXME: z should be obtained from MOST and passed in here

    from Numeric import resize
    z = resize(z,outfile.variables['z'][:].shape)
    outfile.variables['x'][:] = x - geo_ref.get_xllcorner()
    outfile.variables['y'][:] = y - geo_ref.get_yllcorner()
    outfile.variables['z'][:] = z             #FIXME HACK for bacwards compat.
    outfile.variables['elevation'][:] = z
    outfile.variables['volumes'][:] = volumes.astype(Int32) #For Opteron 64



    #Time stepping
    stage = outfile.variables['stage']
    xmomentum = outfile.variables['xmomentum']
    ymomentum = outfile.variables['ymomentum']

    if verbose: print 'Converting quantities'
    n = len(times)
    for j in range(n):
        if verbose and j%((n+10)/10)==0: print '  Doing %d of %d' %(j, n)
        i = 0
        for k in range(number_of_latitudes):      #Y direction
            for l in range(number_of_longitudes): #X direction
                w = zscale*amplitudes[j,k,l]/100 + mean_stage
                stage[j,i] = w
                h = w - z[i]
                xmomentum[j,i] = uspeed[j,k,l]/100*h
                ymomentum[j,i] = vspeed[j,k,l]/100*h
                i += 1

    #outfile.close()

    #FIXME: Refactor using code from file_function.statistics
    #Something like print swwstats(swwname)
    if verbose:
        x = outfile.variables['x'][:]
        y = outfile.variables['y'][:]
        print '------------------------------------------------'
        print 'Statistics of output file:'
        print '  Name: %s' %swwname
        print '  Reference:'
        print '    Lower left corner: [%f, %f]'\
              %(geo_ref.get_xllcorner(), geo_ref.get_yllcorner())
        print ' Start time: %f' %starttime
        print '    Min time: %f' %mint
        print '    Max time: %f' %maxt
        print '  Extent:'
        print '    x [m] in [%f, %f], len(x) == %d'\
              %(min(x.flat), max(x.flat), len(x.flat))
        print '    y [m] in [%f, %f], len(y) == %d'\
              %(min(y.flat), max(y.flat), len(y.flat))
        print '    t [s] in [%f, %f], len(t) == %d'\
              %(min(times), max(times), len(times))
        print '  Quantities [SI units]:'
        for name in ['stage', 'xmomentum', 'ymomentum', 'elevation']:
            q = outfile.variables[name][:].flat
            print '    %s in [%f, %f]' %(name, min(q), max(q))



    outfile.close()





def timefile2netcdf(filename, quantity_names=None, time_as_seconds=False):
    """Template for converting typical text files with time series to
    NetCDF tms file.


    The file format is assumed to be either two fields separated by a comma:

        time [DD/MM/YY hh:mm:ss], value0 value1 value2 ...

    E.g

      31/08/04 00:00:00, 1.328223 0 0
      31/08/04 00:15:00, 1.292912 0 0

    or time (seconds), value0 value1 value2 ...
    
      0.0, 1.328223 0 0
      0.1, 1.292912 0 0

    will provide a time dependent function f(t) with three attributes

    filename is assumed to be the rootname with extenisons .txt and .sww
    """

    import time, calendar
    from Numeric import array
    from anuga.config import time_format
    from anuga.utilities.numerical_tools import ensure_numeric



    fid = open(filename + '.txt')
    line = fid.readline()
    fid.close()

    fields = line.split(',')
    msg = 'File %s must have the format date, value0 value1 value2 ...'
    assert len(fields) == 2, msg

    if not time_as_seconds:
        try:
            starttime = calendar.timegm(time.strptime(fields[0], time_format))
        except ValueError:
            msg = 'First field in file %s must be' %filename
            msg += ' date-time with format %s.\n' %time_format
            msg += 'I got %s instead.' %fields[0]
            raise DataTimeError, msg
    else:
        try:
            starttime = float(fields[0])
        except Error:
            msg = "Bad time format"
            raise DataTimeError, msg


    #Split values
    values = []
    for value in fields[1].split():
        values.append(float(value))

    q = ensure_numeric(values)

    msg = 'ERROR: File must contain at least one independent value'
    assert len(q.shape) == 1, msg



    #Read times proper
    from Numeric import zeros, Float, alltrue
    from anuga.config import time_format
    import time, calendar

    fid = open(filename + '.txt')
    lines = fid.readlines()
    fid.close()

    N = len(lines)
    d = len(q)

    T = zeros(N, Float)       #Time
    Q = zeros((N, d), Float)  #Values

    for i, line in enumerate(lines):
        fields = line.split(',')
        if not time_as_seconds:
            realtime = calendar.timegm(time.strptime(fields[0], time_format))
        else:
             realtime = float(fields[0])
        T[i] = realtime - starttime

        for j, value in enumerate(fields[1].split()):
            Q[i, j] = float(value)

    msg = 'File %s must list time as a monotonuosly ' %filename
    msg += 'increasing sequence'
    assert alltrue( T[1:] - T[:-1] > 0 ), msg

    #Create NetCDF file
    from Scientific.IO.NetCDF import NetCDFFile

    fid = NetCDFFile(filename + '.tms', 'w')


    fid.institution = 'Geoscience Australia'
    fid.description = 'Time series'


    #Reference point
    #Start time in seconds since the epoch (midnight 1/1/1970)
    #FIXME: Use Georef
    fid.starttime = starttime

    # dimension definitions
    #fid.createDimension('number_of_volumes', self.number_of_volumes)
    #fid.createDimension('number_of_vertices', 3)


    fid.createDimension('number_of_timesteps', len(T))

    fid.createVariable('time', Float, ('number_of_timesteps',))

    fid.variables['time'][:] = T

    for i in range(Q.shape[1]):
        try:
            name = quantity_names[i]
        except:
            name = 'Attribute%d'%i

        fid.createVariable(name, Float, ('number_of_timesteps',))
        fid.variables[name][:] = Q[:,i]

    fid.close()


def extent_sww(file_name):
    """
    Read in an sww file.

    Input;
    file_name - the sww file

    Output;
    z - Vector of bed elevation
    volumes - Array.  Each row has 3 values, representing
    the vertices that define the volume
    time - Vector of the times where there is stage information
    stage - array with respect to time and vertices (x,y)
    """


    from Scientific.IO.NetCDF import NetCDFFile

    #Check contents
    #Get NetCDF
    fid = NetCDFFile(file_name, 'r')

    # Get the variables
    x = fid.variables['x'][:]
    y = fid.variables['y'][:]
    stage = fid.variables['stage'][:]
    #print "stage",stage
    #print "stage.shap",stage.shape
    #print "min(stage.flat), mpythonax(stage.flat)",min(stage.flat), max(stage.flat)
    #print "min(stage)",min(stage)

    fid.close()

    return [min(x),max(x),min(y),max(y),min(stage.flat),max(stage.flat)]


def sww2domain(filename,boundary=None,t=None,\
               fail_if_NaN=True,NaN_filler=0\
               ,verbose = False,very_verbose = False):
    """
    Usage: domain = sww2domain('file.sww',t=time (default = last time in file))

    Boundary is not recommended if domain.smooth is not selected, as it
    uses unique coordinates, but not unique boundaries. This means that
    the boundary file will not be compatable with the coordinates, and will
    give a different final boundary, or crash.
    """
    NaN=9.969209968386869e+036
    #initialise NaN.

    from Scientific.IO.NetCDF import NetCDFFile
    from shallow_water import Domain
    from Numeric import asarray, transpose, resize

    if verbose: print 'Reading from ', filename
    fid = NetCDFFile(filename, 'r')    #Open existing file for read
    time = fid.variables['time']       #Timesteps
    if t is None:
        t = time[-1]
    time_interp = get_time_interp(time,t)

    # Get the variables as Numeric arrays
    x = fid.variables['x'][:]             #x-coordinates of vertices
    y = fid.variables['y'][:]             #y-coordinates of vertices
    elevation = fid.variables['elevation']     #Elevation
    stage = fid.variables['stage']     #Water level
    xmomentum = fid.variables['xmomentum']   #Momentum in the x-direction
    ymomentum = fid.variables['ymomentum']   #Momentum in the y-direction

    starttime = fid.starttime[0]
    volumes = fid.variables['volumes'][:] #Connectivity
    coordinates=transpose(asarray([x.tolist(),y.tolist()]))

    conserved_quantities = []
    interpolated_quantities = {}
    other_quantities = []

    # get geo_reference
    #sww files don't have to have a geo_ref
    try:
        geo_reference = Geo_reference(NetCDFObject=fid)
    except: #AttributeError, e:
        geo_reference = None

    if verbose: print '    getting quantities'
    for quantity in fid.variables.keys():
        dimensions = fid.variables[quantity].dimensions
        if 'number_of_timesteps' in dimensions:
            conserved_quantities.append(quantity)
            interpolated_quantities[quantity]=\
                  interpolated_quantity(fid.variables[quantity][:],time_interp)
        else: other_quantities.append(quantity)

    other_quantities.remove('x')
    other_quantities.remove('y')
    other_quantities.remove('z')
    other_quantities.remove('volumes')
    try:
        other_quantities.remove('stage_range')
        other_quantities.remove('xmomentum_range')
        other_quantities.remove('ymomentum_range')
        other_quantities.remove('elevation_range')
    except:
        pass
        

    conserved_quantities.remove('time')

    if verbose: print '    building domain'
    #    From domain.Domain:
    #    domain = Domain(coordinates, volumes,\
    #                    conserved_quantities = conserved_quantities,\
    #                    other_quantities = other_quantities,zone=zone,\
    #                    xllcorner=xllcorner, yllcorner=yllcorner)

    #   From shallow_water.Domain:
    coordinates=coordinates.tolist()
    volumes=volumes.tolist()
    #FIXME:should this be in mesh?(peter row)
    if fid.smoothing == 'Yes': unique = False
    else: unique = True
    if unique:
        coordinates,volumes,boundary=weed(coordinates,volumes,boundary)


    try:
        domain = Domain(coordinates, volumes, boundary)
    except AssertionError, e:
        fid.close()
        msg = 'Domain could not be created: %s. Perhaps use "fail_if_NaN=False and NaN_filler = ..."' %e
        raise DataDomainError, msg

    if not boundary is None:
        domain.boundary = boundary

    domain.geo_reference = geo_reference

    domain.starttime=float(starttime)+float(t)
    domain.time=0.0

    for quantity in other_quantities:
        try:
            NaN = fid.variables[quantity].missing_value
        except:
            pass #quantity has no missing_value number
        X = fid.variables[quantity][:]
        if very_verbose:
            print '       ',quantity
            print '        NaN =',NaN
            print '        max(X)'
            print '       ',max(X)
            print '        max(X)==NaN'
            print '       ',max(X)==NaN
            print ''
        if (max(X)==NaN) or (min(X)==NaN):
            if fail_if_NaN:
                msg = 'quantity "%s" contains no_data entry'%quantity
                raise DataMissingValuesError, msg
            else:
                data = (X<>NaN)
                X = (X*data)+(data==0)*NaN_filler
        if unique:
            X = resize(X,(len(X)/3,3))
        domain.set_quantity(quantity,X)
    #
    for quantity in conserved_quantities:
        try:
            NaN = fid.variables[quantity].missing_value
        except:
            pass #quantity has no missing_value number
        X = interpolated_quantities[quantity]
        if very_verbose:
            print '       ',quantity
            print '        NaN =',NaN
            print '        max(X)'
            print '       ',max(X)
            print '        max(X)==NaN'
            print '       ',max(X)==NaN
            print ''
        if (max(X)==NaN) or (min(X)==NaN):
            if fail_if_NaN:
                msg = 'quantity "%s" contains no_data entry'%quantity
                raise DataMissingValuesError, msg
            else:
                data = (X<>NaN)
                X = (X*data)+(data==0)*NaN_filler
        if unique:
            X = resize(X,(X.shape[0]/3,3))
        domain.set_quantity(quantity,X)

    fid.close()
    return domain

def interpolated_quantity(saved_quantity,time_interp):

    #given an index and ratio, interpolate quantity with respect to time.
    index,ratio = time_interp
    Q = saved_quantity
    if ratio > 0:
        q = (1-ratio)*Q[index]+ ratio*Q[index+1]
    else:
        q = Q[index]
    #Return vector of interpolated values
    return q

def get_time_interp(time,t=None):
    #Finds the ratio and index for time interpolation.
    #It is borrowed from previous abstract_2d_finite_volumes code.
    if t is None:
        t=time[-1]
        index = -1
        ratio = 0.
    else:
        T = time
        tau = t
        index=0
        msg = 'Time interval derived from file %s [%s:%s]'\
            %('FIXMEfilename', T[0], T[-1])
        msg += ' does not match model time: %s' %tau
        if tau < time[0]: raise DataTimeError, msg
        if tau > time[-1]: raise DataTimeError, msg
        while tau > time[index]: index += 1
        while tau < time[index]: index -= 1
        if tau == time[index]:
            #Protect against case where tau == time[-1] (last time)
            # - also works in general when tau == time[i]
            ratio = 0
        else:
            #t is now between index and index+1
            ratio = (tau - time[index])/(time[index+1] - time[index])
    return (index,ratio)


def weed(coordinates,volumes,boundary = None):
    if type(coordinates)==ArrayType:
        coordinates = coordinates.tolist()
    if type(volumes)==ArrayType:
        volumes = volumes.tolist()

    unique = False
    point_dict = {}
    same_point = {}
    for i in range(len(coordinates)):
        point = tuple(coordinates[i])
        if point_dict.has_key(point):
            unique = True
            same_point[i]=point
            #to change all point i references to point j
        else:
            point_dict[point]=i
            same_point[i]=point

    coordinates = []
    i = 0
    for point in point_dict.keys():
        point = tuple(point)
        coordinates.append(list(point))
        point_dict[point]=i
        i+=1


    for volume in volumes:
        for i in range(len(volume)):
            index = volume[i]
            if index>-1:
                volume[i]=point_dict[same_point[index]]

    new_boundary = {}
    if not boundary is None:
        for segment in boundary.keys():
            point0 = point_dict[same_point[segment[0]]]
            point1 = point_dict[same_point[segment[1]]]
            label = boundary[segment]
            #FIXME should the bounday attributes be concaterated
            #('exterior, pond') or replaced ('pond')(peter row)

            if new_boundary.has_key((point0,point1)):
                new_boundary[(point0,point1)]=new_boundary[(point0,point1)]#\
                                              #+','+label

            elif new_boundary.has_key((point1,point0)):
                new_boundary[(point1,point0)]=new_boundary[(point1,point0)]#\
                                              #+','+label
            else: new_boundary[(point0,point1)]=label

        boundary = new_boundary

    return coordinates,volumes,boundary


def decimate_dem(basename_in, stencil, cellsize_new, basename_out=None,
                 verbose=False):
    """Read Digitial Elevation model from the following NetCDF format (.dem)

    Example:

    ncols         3121
    nrows         1800
    xllcorner     722000
    yllcorner     5893000
    cellsize      25
    NODATA_value  -9999
    138.3698 137.4194 136.5062 135.5558 ..........

    Decimate data to cellsize_new using stencil and write to NetCDF dem format.
    """

    import os
    from Scientific.IO.NetCDF import NetCDFFile
    from Numeric import Float, zeros, sum, reshape, equal

    root = basename_in
    inname = root + '.dem'

    #Open existing netcdf file to read
    infile = NetCDFFile(inname, 'r')
    if verbose: print 'Reading DEM from %s' %inname

    #Read metadata
    ncols = infile.ncols[0]
    nrows = infile.nrows[0]
    xllcorner = infile.xllcorner[0]
    yllcorner = infile.yllcorner[0]
    cellsize = infile.cellsize[0]
    NODATA_value = infile.NODATA_value[0]
    zone = infile.zone[0]
    false_easting = infile.false_easting[0]
    false_northing = infile.false_northing[0]
    projection = infile.projection
    datum = infile.datum
    units = infile.units

    dem_elevation = infile.variables['elevation']

    #Get output file name
    if basename_out == None:
        outname = root + '_' + repr(cellsize_new) + '.dem'
    else:
        outname = basename_out + '.dem'

    if verbose: print 'Write decimated NetCDF file to %s' %outname

    #Determine some dimensions for decimated grid
    (nrows_stencil, ncols_stencil) = stencil.shape
    x_offset = ncols_stencil / 2
    y_offset = nrows_stencil / 2
    cellsize_ratio = int(cellsize_new / cellsize)
    ncols_new = 1 + (ncols - ncols_stencil) / cellsize_ratio
    nrows_new = 1 + (nrows - nrows_stencil) / cellsize_ratio

    #Open netcdf file for output
    outfile = NetCDFFile(outname, 'w')

    #Create new file
    outfile.institution = 'Geoscience Australia'
    outfile.description = 'NetCDF DEM format for compact and portable storage ' +\
                           'of spatial point data'
    #Georeferencing
    outfile.zone = zone
    outfile.projection = projection
    outfile.datum = datum
    outfile.units = units

    outfile.cellsize = cellsize_new
    outfile.NODATA_value = NODATA_value
    outfile.false_easting = false_easting
    outfile.false_northing = false_northing

    outfile.xllcorner = xllcorner + (x_offset * cellsize)
    outfile.yllcorner = yllcorner + (y_offset * cellsize)
    outfile.ncols = ncols_new
    outfile.nrows = nrows_new

    # dimension definition
    outfile.createDimension('number_of_points', nrows_new*ncols_new)

    # variable definition
    outfile.createVariable('elevation', Float, ('number_of_points',))

    # Get handle to the variable
    elevation = outfile.variables['elevation']

    dem_elevation_r = reshape(dem_elevation, (nrows, ncols))

    #Store data
    global_index = 0
    for i in range(nrows_new):
        if verbose: print 'Processing row %d of %d' %(i, nrows_new)
        lower_index = global_index
        telev =  zeros(ncols_new, Float)
        local_index = 0
        trow = i * cellsize_ratio

        for j in range(ncols_new):
            tcol = j * cellsize_ratio
            tmp = dem_elevation_r[trow:trow+nrows_stencil, tcol:tcol+ncols_stencil]

            #if dem contains 1 or more NODATA_values set value in
            #decimated dem to NODATA_value, else compute decimated
            #value using stencil
            if sum(sum(equal(tmp, NODATA_value))) > 0:
                telev[local_index] = NODATA_value
            else:
                telev[local_index] = sum(sum(tmp * stencil))

            global_index += 1
            local_index += 1

        upper_index = global_index

        elevation[lower_index:upper_index] = telev

    assert global_index == nrows_new*ncols_new, 'index not equal to number of points'

    infile.close()
    outfile.close()




def tsh2sww(filename, verbose=False): #test_tsh2sww
    """
    to check if a tsh/msh file 'looks' good.
    """


    if verbose == True:print 'Creating domain from', filename
    domain = pmesh_to_domain_instance(filename, Domain)
    if verbose == True:print "Number of triangles = ", len(domain)

    domain.smooth = True
    domain.format = 'sww'   #Native netcdf visualisation format
    file_path, filename = path.split(filename)
    filename, ext = path.splitext(filename)
    domain.set_name(filename)    
    domain.reduction = mean
    if verbose == True:print "file_path",file_path
    if file_path == "":file_path = "."
    domain.set_datadir(file_path)

    if verbose == True:
        print "Output written to " + domain.get_datadir() + sep + \
              domain.get_name() + "." + domain.format
    sww = get_dataobject(domain)
    sww.store_connectivity()
    sww.store_timestep('stage')


def asc_csiro2sww(bath_dir,
                  elevation_dir,
                  ucur_dir,
                  vcur_dir,
                  sww_file,
                  minlat = None, maxlat = None,
                  minlon = None, maxlon = None,
                  zscale=1,
                  mean_stage = 0,
                  fail_on_NaN = True,
                  elevation_NaN_filler = 0,
                  bath_prefix='ba',
                  elevation_prefix='el',
                  verbose=False):
    """
    Produce an sww boundary file, from esri ascii data from CSIRO.

    Also convert latitude and longitude to UTM. All coordinates are
    assumed to be given in the GDA94 datum.

    assume:
    All files are in esri ascii format

    4 types of information
    bathymetry
    elevation
    u velocity
    v velocity

    Assumptions
    The metadata of all the files is the same
    Each type is in a seperate directory
    One bath file with extention .000
    The time period is less than 24hrs and uniform.
    """
    from Scientific.IO.NetCDF import NetCDFFile

    from anuga.coordinate_transforms.redfearn import redfearn

    precision = Float # So if we want to change the precision its done here

    # go in to the bath dir and load the only file,
    bath_files = os.listdir(bath_dir)

    bath_file = bath_files[0]
    bath_dir_file =  bath_dir + os.sep + bath_file
    bath_metadata,bath_grid =  _read_asc(bath_dir_file)

    #Use the date.time of the bath file as a basis for
    #the start time for other files
    base_start = bath_file[-12:]

    #go into the elevation dir and load the 000 file
    elevation_dir_file = elevation_dir  + os.sep + elevation_prefix \
                         + base_start

    elevation_files = os.listdir(elevation_dir)
    ucur_files = os.listdir(ucur_dir)
    vcur_files = os.listdir(vcur_dir)
    elevation_files.sort()
    # the first elevation file should be the
    # file with the same base name as the bath data
    assert elevation_files[0] == 'el' + base_start

    number_of_latitudes = bath_grid.shape[0]
    number_of_longitudes = bath_grid.shape[1]
    number_of_volumes = (number_of_latitudes-1)*(number_of_longitudes-1)*2

    longitudes = [bath_metadata['xllcorner']+x*bath_metadata['cellsize'] \
                  for x in range(number_of_longitudes)]
    latitudes = [bath_metadata['yllcorner']+y*bath_metadata['cellsize'] \
                 for y in range(number_of_latitudes)]

     # reverse order of lat, so the fist lat represents the first grid row
    latitudes.reverse()

    kmin, kmax, lmin, lmax = _get_min_max_indexes(latitudes[:],longitudes[:],
                                                 minlat=minlat, maxlat=maxlat,
                                                 minlon=minlon, maxlon=maxlon)


    bath_grid = bath_grid[kmin:kmax,lmin:lmax]
    latitudes = latitudes[kmin:kmax]
    longitudes = longitudes[lmin:lmax]
    number_of_latitudes = len(latitudes)
    number_of_longitudes = len(longitudes)
    number_of_times = len(os.listdir(elevation_dir))
    number_of_points = number_of_latitudes*number_of_longitudes
    number_of_volumes = (number_of_latitudes-1)*(number_of_longitudes-1)*2

    #Work out the times
    if len(elevation_files) > 1:
        # Assume: The time period is less than 24hrs.
        time_period = (int(elevation_files[1][-3:]) - \
                      int(elevation_files[0][-3:]))*60*60
        times = [x*time_period for x in range(len(elevation_files))]
    else:
        times = [0.0]


    if verbose:
        print '------------------------------------------------'
        print 'Statistics:'
        print '  Extent (lat/lon):'
        print '    lat in [%f, %f], len(lat) == %d'\
              %(min(latitudes), max(latitudes),
                len(latitudes))
        print '    lon in [%f, %f], len(lon) == %d'\
              %(min(longitudes), max(longitudes),
                len(longitudes))
        print '    t in [%f, %f], len(t) == %d'\
              %(min(times), max(times), len(times))

    ######### WRITE THE SWW FILE #############
    # NetCDF file definition
    outfile = NetCDFFile(sww_file, 'w')

    #Create new file
    outfile.institution = 'Geoscience Australia'
    outfile.description = 'Converted from XXX'


    #For sww compatibility
    outfile.smoothing = 'Yes'
    outfile.order = 1

    #Start time in seconds since the epoch (midnight 1/1/1970)
    outfile.starttime = starttime = times[0]


    # dimension definitions
    outfile.createDimension('number_of_volumes', number_of_volumes)

    outfile.createDimension('number_of_vertices', 3)
    outfile.createDimension('number_of_points', number_of_points)
    outfile.createDimension('number_of_timesteps', number_of_times)

    # variable definitions
    outfile.createVariable('x', precision, ('number_of_points',))
    outfile.createVariable('y', precision, ('number_of_points',))
    outfile.createVariable('elevation', precision, ('number_of_points',))

    #FIXME: Backwards compatibility
    outfile.createVariable('z', precision, ('number_of_points',))
    #################################

    outfile.createVariable('volumes', Int, ('number_of_volumes',
                                            'number_of_vertices'))

    outfile.createVariable('time', precision,
                           ('number_of_timesteps',))

    outfile.createVariable('stage', precision,
                           ('number_of_timesteps',
                            'number_of_points'))

    outfile.createVariable('xmomentum', precision,
                           ('number_of_timesteps',
                            'number_of_points'))

    outfile.createVariable('ymomentum', precision,
                           ('number_of_timesteps',
                            'number_of_points'))

    #Store
    from anuga.coordinate_transforms.redfearn import redfearn
    x = zeros(number_of_points, Float)  #Easting
    y = zeros(number_of_points, Float)  #Northing

    if verbose: print 'Making triangular grid'
    #Get zone of 1st point.
    refzone, _, _ = redfearn(latitudes[0],longitudes[0])

    vertices = {}
    i = 0
    for k, lat in enumerate(latitudes):
        for l, lon in enumerate(longitudes):

            vertices[l,k] = i

            zone, easting, northing = redfearn(lat,lon)

            msg = 'Zone boundary crossed at longitude =', lon
            #assert zone == refzone, msg
            #print '%7.2f %7.2f %8.2f %8.2f' %(lon, lat, easting, northing)
            x[i] = easting
            y[i] = northing
            i += 1


    #Construct 2 triangles per 'rectangular' element
    volumes = []
    for l in range(number_of_longitudes-1):    #X direction
        for k in range(number_of_latitudes-1): #Y direction
            v1 = vertices[l,k+1]
            v2 = vertices[l,k]
            v3 = vertices[l+1,k+1]
            v4 = vertices[l+1,k]

            #Note, this is different to the ferrit2sww code
            #since the order of the lats is reversed.
            volumes.append([v1,v3,v2]) #Upper element
            volumes.append([v4,v2,v3]) #Lower element

    volumes = array(volumes)

    geo_ref = Geo_reference(refzone,min(x),min(y))
    geo_ref.write_NetCDF(outfile)

    # This will put the geo ref in the middle
    #geo_ref = Geo_reference(refzone,(max(x)+min(x))/2.0,(max(x)+min(y))/2.)


    if verbose:
        print '------------------------------------------------'
        print 'More Statistics:'
        print '  Extent (/lon):'
        print '    x in [%f, %f], len(lat) == %d'\
              %(min(x), max(x),
                len(x))
        print '    y in [%f, %f], len(lon) == %d'\
              %(min(y), max(y),
                len(y))
        print 'geo_ref: ',geo_ref

    z = resize(bath_grid,outfile.variables['z'][:].shape)
    outfile.variables['x'][:] = x - geo_ref.get_xllcorner()
    outfile.variables['y'][:] = y - geo_ref.get_yllcorner()
    outfile.variables['z'][:] = z
    outfile.variables['elevation'][:] = z  #FIXME HACK
    outfile.variables['volumes'][:] = volumes.astype(Int32) #On Opteron 64

    stage = outfile.variables['stage']
    xmomentum = outfile.variables['xmomentum']
    ymomentum = outfile.variables['ymomentum']

    outfile.variables['time'][:] = times   #Store time relative

    if verbose: print 'Converting quantities'
    n = number_of_times
    for j in range(number_of_times):
        # load in files
        elevation_meta, elevation_grid = \
           _read_asc(elevation_dir + os.sep + elevation_files[j])

        _, u_momentum_grid =  _read_asc(ucur_dir + os.sep + ucur_files[j])
        _, v_momentum_grid =  _read_asc(vcur_dir + os.sep + vcur_files[j])

        #cut matrix to desired size
        elevation_grid = elevation_grid[kmin:kmax,lmin:lmax]
        u_momentum_grid = u_momentum_grid[kmin:kmax,lmin:lmax]
        v_momentum_grid = v_momentum_grid[kmin:kmax,lmin:lmax]
        
        # handle missing values
        missing = (elevation_grid == elevation_meta['NODATA_value'])
        if sometrue (missing):
            if fail_on_NaN:
                msg = 'File %s contains missing values'\
                      %(elevation_files[j])
                raise DataMissingValuesError, msg
            else:
                elevation_grid = elevation_grid*(missing==0) + \
                                 missing*elevation_NaN_filler


        if verbose and j%((n+10)/10)==0: print '  Doing %d of %d' %(j, n)
        i = 0
        for k in range(number_of_latitudes):      #Y direction
            for l in range(number_of_longitudes): #X direction
                w = zscale*elevation_grid[k,l] + mean_stage
                stage[j,i] = w
                h = w - z[i]
                xmomentum[j,i] = u_momentum_grid[k,l]*h
                ymomentum[j,i] = v_momentum_grid[k,l]*h
                i += 1
    outfile.close()

def _get_min_max_indexes(latitudes_ref,longitudes_ref,
                        minlat=None, maxlat=None,
                        minlon=None, maxlon=None):
    """
    return max, min indexes (for slicing) of the lat and long arrays to cover the area
    specified with min/max lat/long

    Think of the latitudes and longitudes describing a 2d surface.
    The area returned is, if possible, just big enough to cover the
    inputed max/min area. (This will not be possible if the max/min area
    has a section outside of the latitudes/longitudes area.)

    asset  longitudes are sorted,
    long - from low to high (west to east, eg 148 - 151)
    assert latitudes are sorted, ascending or decending
    """
    latitudes = latitudes_ref[:]
    longitudes = longitudes_ref[:]

    latitudes = ensure_numeric(latitudes)
    longitudes = ensure_numeric(longitudes)
    
    assert allclose(sort(longitudes), longitudes)
    
    lat_ascending = True
    if not allclose(sort(latitudes), latitudes):
        lat_ascending = False
        # reverse order of lat, so it's in ascending order          
        latitudes = latitudes[::-1]
        assert allclose(sort(latitudes), latitudes)
    #print "latitudes  in funct", latitudes
    
    largest_lat_index = len(latitudes)-1
    #Cut out a smaller extent.
    if minlat == None:
        lat_min_index = 0
    else:
        lat_min_index = searchsorted(latitudes, minlat)-1
        if lat_min_index <0:
            lat_min_index = 0


    if maxlat == None:
        lat_max_index = largest_lat_index #len(latitudes)
    else:
        lat_max_index = searchsorted(latitudes, maxlat)
        if lat_max_index > largest_lat_index:
            lat_max_index = largest_lat_index

    if minlon == None:
        lon_min_index = 0
    else:
        lon_min_index = searchsorted(longitudes, minlon)-1
        if lon_min_index <0:
            lon_min_index = 0

    if maxlon == None:
        lon_max_index = len(longitudes)
    else:
        lon_max_index = searchsorted(longitudes, maxlon)

    # Reversing the indexes, if the lat array is decending
    if lat_ascending is False:
        lat_min_index, lat_max_index = largest_lat_index - lat_max_index , \
                                       largest_lat_index - lat_min_index
    lat_max_index = lat_max_index + 1 # taking into account how slicing works
    lon_max_index = lon_max_index + 1 # taking into account how slicing works

    return lat_min_index, lat_max_index, lon_min_index, lon_max_index


def _read_asc(filename, verbose=False):
    """Read esri file from the following ASCII format (.asc)

    Example:

    ncols         3121
    nrows         1800
    xllcorner     722000
    yllcorner     5893000
    cellsize      25
    NODATA_value  -9999
    138.3698 137.4194 136.5062 135.5558 ..........

    """

    datafile = open(filename)

    if verbose: print 'Reading DEM from %s' %(filename)
    lines = datafile.readlines()
    datafile.close()

    if verbose: print 'Got', len(lines), ' lines'

    ncols = int(lines.pop(0).split()[1].strip())
    nrows = int(lines.pop(0).split()[1].strip())
    xllcorner = float(lines.pop(0).split()[1].strip())
    yllcorner = float(lines.pop(0).split()[1].strip())
    cellsize = float(lines.pop(0).split()[1].strip())
    NODATA_value = float(lines.pop(0).split()[1].strip())

    assert len(lines) == nrows

    #Store data
    grid = []

    n = len(lines)
    for i, line in enumerate(lines):
        cells = line.split()
        assert len(cells) == ncols
        grid.append(array([float(x) for x in cells]))
    grid = array(grid)

    return {'xllcorner':xllcorner,
            'yllcorner':yllcorner,
            'cellsize':cellsize,
            'NODATA_value':NODATA_value}, grid



    ####  URS 2 SWW  ###

lon_name = 'LON'
lat_name = 'LAT'
time_name = 'TIME'
precision = Float # So if we want to change the precision its done here        
class Write_nc:
    """
    Write an nc file.
    
    Note, this should be checked to meet cdc netcdf conventions for gridded
    data. http://www.cdc.noaa.gov/cdc/conventions/cdc_netcdf_standard.shtml
    
    """
    def __init__(self,
                 quantity_name,
                 file_name,
                 time_step_count,
                 time_step,
                 lon,
                 lat):
        """
        time_step_count is the number of time steps.
        time_step is the time step size
        
        pre-condition: quantity_name must be 'HA' 'UA'or 'VA'.
        """
        self.quantity_name = quantity_name
        quantity_units = {'HA':'CENTIMETERS',
                              'UA':'CENTIMETERS/SECOND',
                              'VA':'CENTIMETERS/SECOND'}       
        
        multiplier_dic = {'HA':100.0, # To convert from m to cm
                              'UA':100.0,  #  m/s to cm/sec
                              'VA':-100.0}  # MUX files have positve x in the
        # Southern direction.  This corrects for it, when writing nc files.
        
        self.quantity_multiplier =  multiplier_dic[self.quantity_name]
        
        #self.file_name = file_name
        self.time_step_count = time_step_count
        self.time_step = time_step

        # NetCDF file definition
        self.outfile = NetCDFFile(file_name, 'w')
        outfile = self.outfile       

        #Create new file
        nc_lon_lat_header(outfile, lon, lat)
    
        # TIME
        outfile.createDimension(time_name, None)
        outfile.createVariable(time_name, precision, (time_name,))

        #QUANTITY
        outfile.createVariable(self.quantity_name, precision,
                               (time_name, lat_name, lon_name))
        outfile.variables[self.quantity_name].missing_value=-1.e+034
        outfile.variables[self.quantity_name].units= \
                                 quantity_units[self.quantity_name]
        outfile.variables[lon_name][:]= ensure_numeric(lon)
        outfile.variables[lat_name][:]= ensure_numeric(lat)

        #Assume no one will be wanting to read this, while we are writing
        #outfile.close()
        
    def store_timestep(self, quantity_slice):
        """
        Write a time slice of quantity info 
        quantity_slice is the data to be stored at this time step
        """
        
        outfile = self.outfile
        
        # Get the variables
        time = outfile.variables[time_name]
        quantity = outfile.variables[self.quantity_name]
            
        i = len(time)

        #Store time
        time[i] = i*self.time_step #self.domain.time
        quantity[i,:] = quantity_slice* self.quantity_multiplier
        
    def close(self):
        self.outfile.close()

def urs2sww(basename_in='o', basename_out=None, verbose=False,
            remove_nc_files=True,
            minlat=None, maxlat=None,
            minlon= None, maxlon=None,
            mint=None, maxt=None,
            mean_stage=0,
            origin = None,
            zscale=1,
            fail_on_NaN=True,
            NaN_filler=0,
            elevation=None):
    """
    Convert URS C binary format for wave propagation to
    sww format native to abstract_2d_finite_volumes.

    Specify only basename_in and read files of the form
    basefilename_velocity-z-mux, basefilename_velocity-e-mux and
    basefilename_waveheight-n-mux containing relative height,
    x-velocity and y-velocity, respectively.

    Also convert latitude and longitude to UTM. All coordinates are
    assumed to be given in the GDA94 datum. The latitude and longitude
    information is for  a grid.

    min's and max's: If omitted - full extend is used.
    To include a value min may equal it, while max must exceed it.
    Lat and lon are assumed to be in decimal degrees. 
    NOTE: minlon is the most east boundary.
    
    origin is a 3-tuple with geo referenced
    UTM coordinates (zone, easting, northing)
    It will be the origin of the sww file. This shouldn't be used,
    since all of anuga should be able to handle an arbitary origin.


    URS C binary format has data orgainised as TIME, LONGITUDE, LATITUDE
    which means that latitude is the fastest
    varying dimension (row major order, so to speak)

    In URS C binary the latitudes and longitudes are in assending order.
    """
    if basename_out == None:
        basename_out = basename_in
    files_out = urs2nc(basename_in, basename_out)
    ferret2sww(basename_out,
               minlat=minlat,
               maxlat=maxlat,
               minlon=minlon,
               maxlon=maxlon,
               mint=mint,
               maxt=maxt,
               mean_stage=mean_stage,
               origin=origin,
               zscale=zscale,
               fail_on_NaN=fail_on_NaN,
               NaN_filler=NaN_filler,
               inverted_bathymetry=True,
               verbose=verbose)
    #print "files_out",files_out
    if remove_nc_files:
        for file_out in files_out:
            os.remove(file_out)
    
def urs2nc(basename_in = 'o', basename_out = 'urs'):
    """
    Convert the 3 urs files to 4 nc files.

    The name of the urs file names must be;
    [basename_in]_velocity-z-mux
    [basename_in]_velocity-e-mux
    [basename_in]_waveheight-n-mux
    
    """
    
    files_in = [basename_in + WAVEHEIGHT_MUX_LABEL,
                basename_in + EAST_VELOCITY_LABEL,
                basename_in + NORTH_VELOCITY_LABEL]
    files_out = [basename_out+'_ha.nc',
                 basename_out+'_ua.nc',
                 basename_out+'_va.nc']
    quantities = ['HA','UA','VA']

    for file_name in files_in:
        if os.access(file_name, os.F_OK) == 0 :
            msg = 'File %s does not exist or is not accessible' %file_name
            raise IOError, msg
        
    hashed_elevation = None
    for file_in, file_out, quantity in map(None, files_in,
                                           files_out,
                                           quantities):
        lonlatdep, lon, lat, depth = _binary_c2nc(file_in,
                                         file_out,
                                         quantity)
        #print "lonlatdep", lonlatdep 
        if hashed_elevation == None:
            elevation_file = basename_out+'_e.nc'
            write_elevation_nc(elevation_file,
                                lon,
                                lat,
                                depth)
            hashed_elevation = myhash(lonlatdep)
        else:
            msg = "The elevation information in the mux files is inconsistent"
            assert hashed_elevation == myhash(lonlatdep), msg
    files_out.append(elevation_file)
    return files_out
    
def _binary_c2nc(file_in, file_out, quantity):
    """
    Reads in a quantity urs file and writes a quantity nc file.
    additionally, returns the depth and lat, long info,
    so it can be written to a file.
    """
    columns = 3 # long, lat , depth
    mux_file = open(file_in, 'rb')
    
    # Number of points/stations
    (points_num,)= unpack('i',mux_file.read(4))

    # nt, int - Number of time steps
    (time_step_count,)= unpack('i',mux_file.read(4))

    #dt, float - time step, seconds
    (time_step,) = unpack('f', mux_file.read(4))
    
    msg = "Bad data in the mux file."
    if points_num < 0:
        mux_file.close()
        raise ANUGAError, msg
    if time_step_count < 0:
        mux_file.close()
        raise ANUGAError, msg
    if time_step < 0:
        mux_file.close()
        raise ANUGAError, msg
    
    lonlatdep = p_array.array('f')
    lonlatdep.read(mux_file, columns * points_num)
    lonlatdep = array(lonlatdep, typecode=Float)    
    lonlatdep = reshape(lonlatdep, (points_num, columns))
    
    lon, lat, depth = lon_lat2grid(lonlatdep)
    lon_sorted = list(lon)
    lon_sorted.sort()

    if not lon == lon_sorted:
        msg = "Longitudes in mux file are not in ascending order"
        raise IOError, msg
    lat_sorted = list(lat)
    lat_sorted.sort()

    if not lat == lat_sorted:
        msg = "Latitudes in mux file are not in ascending order"
    
    nc_file = Write_nc(quantity,
                       file_out,
                       time_step_count,
                       time_step,
                       lon,
                       lat)

    for i in range(time_step_count):
        #Read in a time slice  from mux file  
        hz_p_array = p_array.array('f')
        hz_p_array.read(mux_file, points_num)
        hz_p = array(hz_p_array, typecode=Float)
        hz_p = reshape(hz_p, (len(lon), len(lat)))
        hz_p = transpose(hz_p) #mux has lat varying fastest, nc has long v.f. 

        #write time slice to nc file
        nc_file.store_timestep(hz_p)
    mux_file.close()
    nc_file.close()

    return lonlatdep, lon, lat, depth
    

def write_elevation_nc(file_out, lon, lat, depth_vector):
    """
    Write an nc elevation file.
    """
    
    # NetCDF file definition
    outfile = NetCDFFile(file_out, 'w')

    #Create new file
    nc_lon_lat_header(outfile, lon, lat)
    
    # ELEVATION
    zname = 'ELEVATION'
    outfile.createVariable(zname, precision, (lat_name, lon_name))
    outfile.variables[zname].units='CENTIMETERS'
    outfile.variables[zname].missing_value=-1.e+034

    outfile.variables[lon_name][:]= ensure_numeric(lon)
    outfile.variables[lat_name][:]= ensure_numeric(lat)

    depth = reshape(depth_vector, ( len(lat), len(lon)))
    outfile.variables[zname][:]= depth
    
    outfile.close()
    
def nc_lon_lat_header(outfile, lon, lat):
    """
    outfile is the netcdf file handle.
    lon - a list/array of the longitudes
    lat - a list/array of the latitudes
    """
    
    outfile.institution = 'Geoscience Australia'
    outfile.description = 'Converted from URS binary C'
    
    # Longitude
    outfile.createDimension(lon_name, len(lon))
    outfile.createVariable(lon_name, precision, (lon_name,))
    outfile.variables[lon_name].point_spacing='uneven'
    outfile.variables[lon_name].units='degrees_east'
    outfile.variables[lon_name].assignValue(lon)


    # Latitude
    outfile.createDimension(lat_name, len(lat))
    outfile.createVariable(lat_name, precision, (lat_name,))
    outfile.variables[lat_name].point_spacing='uneven'
    outfile.variables[lat_name].units='degrees_north'
    outfile.variables[lat_name].assignValue(lat)


    
def lon_lat2grid(long_lat_dep):
    """
    given a list of points that are assumed to be an a grid,
    return the long's and lat's of the grid.
    long_lat_dep is an array where each row is a position.
    The first column is longitudes.
    The second column is latitudes.

    The latitude is the fastest varying dimension - in mux files
    """
    LONG = 0
    LAT = 1
    QUANTITY = 2

    long_lat_dep = ensure_numeric(long_lat_dep, Float)
    
    num_points = long_lat_dep.shape[0]
    this_rows_long = long_lat_dep[0,LONG]

    # Count the length of unique latitudes
    i = 0
    while long_lat_dep[i,LONG] == this_rows_long and i < num_points:
        i += 1
    # determine the lats and longsfrom the grid
    lat = long_lat_dep[:i, LAT]        
    long = long_lat_dep[::i, LONG]
    
    lenlong = len(long)
    lenlat = len(lat)
    #print 'len lat', lat, len(lat)
    #print 'len long', long, len(long) 
          
    msg = 'Input data is not gridded'      
    assert num_points % lenlat == 0, msg
    assert num_points % lenlong == 0, msg
          
    # Test that data is gridded        
    for i in range(lenlong):
        msg = 'Data is not gridded.  It must be for this operation'
        first = i*lenlat
        last = first + lenlat
                
        assert allclose(long_lat_dep[first:last,LAT], lat), msg
        assert allclose(long_lat_dep[first:last,LONG], long[i]), msg
    
    
#    print 'range long', min(long), max(long)
#    print 'range lat', min(lat), max(lat)
#    print 'ref long', min(long_lat_dep[:,0]), max(long_lat_dep[:,0])
#    print 'ref lat', min(long_lat_dep[:,1]), max(long_lat_dep[:,1])
    
   
    
    msg = 'Out of range latitudes/longitudes'
    for l in lat:assert -90 < l < 90 , msg
    for l in long:assert -180 < l < 180 , msg

    # Changing quantity from lat being the fastest varying dimension to
    # long being the fastest varying dimension
    # FIXME - make this faster/do this a better way
    # use numeric transpose, after reshaping the quantity vector
#    quantity = zeros(len(long_lat_dep), Float)
    quantity = zeros(num_points, Float)
    
#    print 'num',num_points
    for lat_i, _ in enumerate(lat):
        for long_i, _ in enumerate(long):
            q_index = lat_i*lenlong+long_i
            lld_index = long_i*lenlat+lat_i
#            print 'lat_i', lat_i, 'long_i',long_i, 'q_index', q_index, 'lld_index', lld_index
            temp = long_lat_dep[lld_index, QUANTITY]
            quantity[q_index] = temp
            
    return long, lat, quantity

    ####  END URS 2 SWW  ###

    #### URS UNGRIDDED 2 SWW ###

    ### PRODUCING THE POINTS NEEDED FILE ###
LL_LAT = -50.0
LL_LONG = 80.0
GRID_SPACING = 1.0/60.0
LAT_AMOUNT = 4800
LONG_AMOUNT = 3600
def URS_points_needed_to_file(file_name, boundary_polygon, zone,
                              ll_lat=LL_LAT, ll_long=LL_LONG,
                              grid_spacing=GRID_SPACING, 
                              lat_amount=LAT_AMOUNT, long_amount=LONG_AMOUNT,
                              export_csv=False, use_cache=False,
                              verbose=False):
    """
    file_name - name of the urs file produced for David.
    boundary_polygon - a list of points that describes a polygon.
                      The last point is assumed ot join the first point.
                      This is in UTM (lat long would be better though)

    ll_lat - lower left latitude, in decimal degrees
    ll-long - lower left longitude, in decimal degrees
    grid_spacing - in deciamal degrees


    Don't add the file extension.  It will be added.
    """
    geo = URS_points_needed(boundary_polygon, zone, ll_lat, ll_long,
                            grid_spacing, 
                      lat_amount, long_amount,use_cache, verbose)
    if not file_name[-4:] == ".urs":
        file_name += ".urs"
    geo.export_points_file(file_name)
    if export_csv:
        if file_name[-4:] == ".urs":
            file_name = file_name[:-4] + ".csv"
        geo.export_points_file(file_name)

def URS_points_needed(boundary_polygon, zone, ll_lat=LL_LAT,
                      ll_long=LL_LONG, grid_spacing=GRID_SPACING, 
                      lat_amount=LAT_AMOUNT, long_amount=LONG_AMOUNT,
                      use_cache=False, verbose=False):
    args = (boundary_polygon,
                      zone)
    kwargs = {'ll_lat': ll_lat,
              'll_long': ll_long,
              'grid_spacing': grid_spacing,
              'lat_amount': lat_amount,
              'long_amount': long_amount}  
    if use_cache is True:
        try:
            from anuga.caching import cache
        except:
            msg = 'Caching was requested, but caching module'+\
                  'could not be imported'
            raise msg


        geo = cache(_URS_points_needed,
                  args, kwargs,
                  verbose=verbose,
                  compression=False)
    else:
        #I was getting 'got multiple values for keyword argument' errors
        #geo = apply(_URS_points_needed, args, kwargs)
        geo = _URS_points_needed(boundary_polygon,
                      zone, ll_lat,
                      ll_long, grid_spacing, 
                      lat_amount, long_amount)

    return geo    
def _URS_points_needed(boundary_polygon,
                      zone, ll_lat=LL_LAT,
                      ll_long=LL_LONG, grid_spacing=GRID_SPACING, 
                      lat_amount=LAT_AMOUNT, long_amount=LONG_AMOUNT):
    """

    boundary_polygon - a list of points that describes a polygon.
                      The last point is assumed ot join the first point.
                      This is in UTM (lat long would be better though)

    ll_lat - lower left latitude, in decimal degrees
    ll-long - lower left longitude, in decimal degrees
    grid_spacing - in deciamal degrees

    """
    
    from sets import ImmutableSet
    
    msg = "grid_spacing can not be zero"
    assert not grid_spacing ==0, msg 
    a = boundary_polygon
    # List of segments.  Each segment is two points.
    segs = [i and [a[i-1], a[i]] or [a[len(a)-1], a[0]] for i in range(len(a))]

    # convert the segs to Lat's and longs.
    
    # Don't assume the zone of the segments is the same as the lower left
    # corner of the lat long data!!  They can easily be in different zones
    
    lat_long_set = ImmutableSet()
    for seg in segs:
        points_lat_long = points_needed(seg, ll_lat, ll_long, grid_spacing, 
                      lat_amount, long_amount, zone)
        lat_long_set |= ImmutableSet(points_lat_long)
    #print "lat_long_set",lat_long_set 
    geo = Geospatial_data(data_points=list(lat_long_set),
                              points_are_lats_longs=True)
    return geo
    
def points_needed(seg, ll_lat, ll_long, grid_spacing, 
                  lat_amount, long_amount, zone):
    """
    return a list of the points, in lats and longs that are needed to
    interpolate any point on the segment.
    """
    from math import sqrt
    #print "zone",zone 
    geo_reference = Geo_reference(zone=zone)
    #print "seg",seg 
    geo = Geospatial_data(seg,geo_reference=geo_reference)
    seg_lat_long = geo.get_data_points(as_lat_long=True)
    #print "seg_lat_long", seg_lat_long
    # 1.415 = 2^0.5, rounded up....
    sqrt_2_rounded_up = 1.415
    buffer = sqrt_2_rounded_up * grid_spacing
    
    max_lat = max(seg_lat_long[0][0], seg_lat_long[1][0]) + buffer
    max_long = max(seg_lat_long[0][1], seg_lat_long[1][1]) + buffer
    min_lat = min(seg_lat_long[0][0], seg_lat_long[1][0]) - buffer
    min_long = min(seg_lat_long[0][1], seg_lat_long[1][1]) - buffer

    first_row = (min_long - ll_long)/grid_spacing
    # To round up
    first_row_long = int(round(first_row + 0.5))
    #print "first_row", first_row_long

    last_row = (max_long - ll_long)/grid_spacing # round down
    last_row_long = int(round(last_row))
    #print "last_row",last_row _long
    
    first_row = (min_lat - ll_lat)/grid_spacing
    # To round up
    first_row_lat = int(round(first_row + 0.5))
    #print "first_row", first_row_lat

    last_row = (max_lat - ll_lat)/grid_spacing # round down
    last_row_lat = int(round(last_row))
    #print "last_row",last_row_lat

    # to work out the max distance -
    # 111120 - horizontal distance between 1 deg latitude. 
    #max_distance = sqrt_2_rounded_up * 111120 * grid_spacing
    max_distance = 157147.4112 * grid_spacing
    #print "max_distance", max_distance #2619.12 m for 1 minute
    points_lat_long = []
    # Create a list of the lat long points to include.
    for index_lat in range(first_row_lat, last_row_lat + 1):
        for index_long in range(first_row_long, last_row_long + 1):
            lat = ll_lat + index_lat*grid_spacing
            long = ll_long + index_long*grid_spacing

            #filter here to keep good points
            if keep_point(lat, long, seg, max_distance):
                points_lat_long.append((lat, long)) #must be hashable
    #print "points_lat_long", points_lat_long

    # Now that we have these points, lets throw ones out that are too far away
    return points_lat_long

def keep_point(lat, long, seg, max_distance):
    """
    seg is two points, UTM
    """
    from math import sqrt
    _ , x0, y0 = redfearn(lat, long)
    x1 = seg[0][0]
    y1 = seg[0][1]
    x2 = seg[1][0]
    y2 = seg[1][1]

    x2_1 = x2-x1
    y2_1 = y2-y1
    d = abs((x2_1)*(y1-y0)-(x1-x0)*(y2_1))/sqrt((x2_1)*(x2_1)+(y2_1)*(y2_1))
    if d <= max_distance:
        return True
    else:
        return False
    
    #### CONVERTING UNGRIDDED URS DATA TO AN SWW FILE ####
   
WAVEHEIGHT_MUX_LABEL = '_waveheight-z-mux'
EAST_VELOCITY_LABEL =  '_velocity-e-mux'
NORTH_VELOCITY_LABEL =  '_velocity-n-mux' 
def urs_ungridded2sww(basename_in='o', basename_out=None, verbose=False,
                      mint=None, maxt=None,
                      mean_stage=0,
                      origin=None,
                      hole_points_UTM=None,
                      zscale=1):
    """   
    Convert URS C binary format for wave propagation to
    sww format native to abstract_2d_finite_volumes.


    Specify only basename_in and read files of the form
    basefilename_velocity-z-mux, basefilename_velocity-e-mux and
    basefilename_waveheight-n-mux containing relative height,
    x-velocity and y-velocity, respectively.

    Also convert latitude and longitude to UTM. All coordinates are
    assumed to be given in the GDA94 datum. The latitude and longitude
    information is assumed ungridded grid.

    min's and max's: If omitted - full extend is used.
    To include a value min ans max may equal it.
    Lat and lon are assumed to be in decimal degrees. 
    
    origin is a 3-tuple with geo referenced
    UTM coordinates (zone, easting, northing)
    It will be the origin of the sww file. This shouldn't be used,
    since all of anuga should be able to handle an arbitary origin.
    The mux point info is NOT relative to this origin.


    URS C binary format has data orgainised as TIME, LONGITUDE, LATITUDE
    which means that latitude is the fastest
    varying dimension (row major order, so to speak)

    In URS C binary the latitudes and longitudes are in assending order.

    Note, interpolations of the resulting sww file will be different
    from results of urs2sww.  This is due to the interpolation
    function used, and the different grid structure between urs2sww
    and this function.
    
    Interpolating data that has an underlying gridded source can
    easily end up with different values, depending on the underlying
    mesh.

    consider these 4 points
    50  -50

    0     0

    The grid can be
     -
    |\|    A
     -
     or;
      - 
     |/|   B 
      -
      If a point is just below the center of the midpoint, it will have a
      +ve value in grid A and a -ve value in grid B.
    """ 
    from anuga.pmesh.mesh import Mesh, NoTrianglesError

    files_in = [basename_in + WAVEHEIGHT_MUX_LABEL,
                basename_in + EAST_VELOCITY_LABEL,
                basename_in + NORTH_VELOCITY_LABEL]
    quantities = ['HA','UA','VA']

    # instanciate urs_points of the three mux files.
    mux = {}
    for quantity, file in map(None, quantities, files_in):
        mux[quantity] = Urs_points(file)
        
    # Could check that the depth is the same. (hashing)

    # handle to a mux file to do depth stuff
    a_mux = mux[quantities[0]]
    
    # Convert to utm
    lat = a_mux.lonlatdep[:,1]
    long = a_mux.lonlatdep[:,0]
    points_utm, zone = convert_from_latlon_to_utm( \
        latitudes=lat, longitudes=long)
    #print "points_utm", points_utm
    #print "zone", zone

    elevation = a_mux.lonlatdep[:,2] * -1 #
    
    # grid ( create a mesh from the selected points)
    # This mesh has a problem.  Triangles are streched over ungridded areas.
    #  If these areas could be described as holes in pmesh, that would be great

    # I can't just get the user to selection a point in the middle.
    # A boundary is needed around these points.
    # But if the zone of points is obvious enough auto-segment should do
    # a good boundary.
    mesh = Mesh()
    mesh.add_vertices(points_utm)
    mesh.auto_segment(smooth_indents=True, expand_pinch=True)
    # To try and avoid alpha shape 'hugging' too much
    mesh.auto_segment( mesh.shape.get_alpha()*1.1 )
    if hole_points_UTM is not None:
        point = ensure_absolute(hole_points_UTM)
        mesh.add_hole(point[0], point[1])
    try:
        mesh.generate_mesh(minimum_triangle_angle=0.0, verbose=False)
    except NoTrianglesError:
        # This is a bit of a hack, going in and changing the
        # data structure.
        mesh.holes = []
        mesh.generate_mesh(minimum_triangle_angle=0.0, verbose=False)
    mesh_dic = mesh.Mesh2MeshList()

    #mesh.export_mesh_file(basename_in + '_168.tsh')
    #import sys; sys.exit() 
    # These are the times of the mux file
    mux_times = []
    for i in range(a_mux.time_step_count):
        mux_times.append(a_mux.time_step * i)  
    mux_times_start_i, mux_times_fin_i = mux2sww_time(mux_times, mint, maxt)
    times = mux_times[mux_times_start_i:mux_times_fin_i]
    
    if mux_times_start_i == mux_times_fin_i:
        # Close the mux files
        for quantity, file in map(None, quantities, files_in):
            mux[quantity].close()
        msg="Due to mint and maxt there's no time info in the boundary SWW."
        raise Exception, msg
        
    # If this raise is removed there is currently no downstream errors
           
    points_utm=ensure_numeric(points_utm)
    #print "mesh_dic['generatedpointlist']", mesh_dic['generatedpointlist']
    #print "points_utm", points_utm 
    assert ensure_numeric(mesh_dic['generatedpointlist']) == \
           ensure_numeric(points_utm)
    
    volumes = mesh_dic['generatedtrianglelist']
    
    # write sww intro and grid stuff.   
    if basename_out is None:
        swwname = basename_in + '.sww'
    else:
        swwname = basename_out + '.sww'

    if verbose: print 'Output to ', swwname
    outfile = NetCDFFile(swwname, 'w')
    # For a different way of doing this, check out tsh2sww
    # work out sww_times and the index range this covers
    sww = Write_sww()
    sww.store_header(outfile, times, len(volumes), len(points_utm),
                     verbose=verbose)
    outfile.mean_stage = mean_stage
    outfile.zscale = zscale

    sww.store_triangulation(outfile, points_utm, volumes,
                            elevation, zone,  new_origin=origin,
                            verbose=verbose)
    
    if verbose: print 'Converting quantities'
    j = 0
    # Read in a time slice from each mux file and write it to the sww file
    for ha, ua, va in map(None, mux['HA'], mux['UA'], mux['VA']):
        if j >= mux_times_start_i and j < mux_times_fin_i:
            stage = zscale*ha + mean_stage
            h = stage - elevation
            xmomentum = ua*h
            ymomentum = -1*va*h # -1 since in mux files south is positive.
            sww.store_quantities(outfile, 
                                 slice_index=j - mux_times_start_i,
                                 verbose=verbose,
                                 stage=stage,
                                 xmomentum=xmomentum,
                                 ymomentum=ymomentum)
        j += 1
    if verbose: sww.verbose_quantities(outfile)
    outfile.close()
    #
    # Do some conversions while writing the sww file

    
def mux2sww_time(mux_times, mint, maxt):
    """
    """

    if mint == None:
        mux_times_start_i = 0
    else:
        mux_times_start_i = searchsorted(mux_times, mint)
       
    if maxt == None:
        mux_times_fin_i = len(mux_times)
    else:
        maxt += 0.5 # so if you specify a time where there is
                    # data that time will be included
        mux_times_fin_i = searchsorted(mux_times, maxt)

    return mux_times_start_i, mux_times_fin_i


class Write_sww:
    from anuga.shallow_water.shallow_water_domain import Domain

    # FIXME (Ole): Hardwiring the conserved quantities like
    # this could be a problem. I would prefer taking them from
    # the instantiation of Domain.
    #
    # (DSG) There is not always a Domain instance when Write_sww is used.
    # Check to see if this is the same level of hardwiring as is in
    # shallow water doamain.
    
    sww_quantities = Domain.conserved_quantities


    RANGE = '_range'
    EXTREMA = ':extrema'

    def __init__(self):
        pass
    
    def store_header(self,
                     outfile,
                     times,
                     number_of_volumes,
                     number_of_points,
                     description='Converted from XXX',
                     smoothing=True,
                     order=1, verbose=False):
        """
        outfile - the name of the file that will be written
        times - A list of the time slice times OR a start time
        Note, if a list is given the info will be made relative.
        number_of_volumes - the number of triangles
        """
    
        outfile.institution = 'Geoscience Australia'
        outfile.description = description

        # For sww compatibility
        if smoothing is True:
            # Smoothing to be depreciated
            outfile.smoothing = 'Yes'
            outfile.vertices_are_stored_uniquely = 'False'
        else:
            # Smoothing to be depreciated
            outfile.smoothing = 'No'
            outfile.vertices_are_stored_uniquely = 'True'
        outfile.order = order

        try:
            revision_number = get_revision_number()
        except:
            revision_number = None
        # Allow None to be stored as a string                
        outfile.revision_number = str(revision_number) 


        
        # times - A list or array of the time slice times OR a start time
        # times = ensure_numeric(times) 
        # Start time in seconds since the epoch (midnight 1/1/1970)

        # This is being used to seperate one number from a list.
        # what it is actually doing is sorting lists from numeric arrays.
        if type(times) is list or type(times) is ArrayType:  
            number_of_times = len(times)
            times = ensure_numeric(times)  
            if number_of_times == 0:
                starttime = 0
            else:
                starttime = times[0]
                times = times - starttime  #Store relative times
        else:
            number_of_times = 0
            starttime = times
            #times = ensure_numeric([])
        outfile.starttime = starttime
        # dimension definitions
        outfile.createDimension('number_of_volumes', number_of_volumes)
        outfile.createDimension('number_of_vertices', 3)
        outfile.createDimension('numbers_in_range', 2)
    
        if smoothing is True:
            outfile.createDimension('number_of_points', number_of_points)
        
            # FIXME(Ole): This will cause sww files for paralle domains to
            # have ghost nodes stored (but not used by triangles).
            # To clean this up, we have to change get_vertex_values and 
            # friends in quantity.py (but I can't be bothered right now)
        else:
            outfile.createDimension('number_of_points', 3*number_of_volumes)
        outfile.createDimension('number_of_timesteps', number_of_times)

        # variable definitions
        outfile.createVariable('x', precision, ('number_of_points',))
        outfile.createVariable('y', precision, ('number_of_points',))
        outfile.createVariable('elevation', precision, ('number_of_points',))
        q = 'elevation'
        outfile.createVariable(q+Write_sww.RANGE, precision,
                               ('numbers_in_range',))


        # Initialise ranges with small and large sentinels.
        # If this was in pure Python we could have used None sensibly
        outfile.variables[q+Write_sww.RANGE][0] = max_float  # Min               
        outfile.variables[q+Write_sww.RANGE][1] = -max_float # Max

        # FIXME: Backwards compatibility
        outfile.createVariable('z', precision, ('number_of_points',))
        #################################

        outfile.createVariable('volumes', Int, ('number_of_volumes',
                                                'number_of_vertices'))

        outfile.createVariable('time', precision,
                               ('number_of_timesteps',))
        
        for q in Write_sww.sww_quantities:
            outfile.createVariable(q, precision,
                                   ('number_of_timesteps',
                                    'number_of_points'))  
            outfile.createVariable(q+Write_sww.RANGE, precision,
                                   ('numbers_in_range',))

            # Initialise ranges with small and large sentinels.
            # If this was in pure Python we could have used None sensibly
            outfile.variables[q+Write_sww.RANGE][0] = max_float  # Min               
            outfile.variables[q+Write_sww.RANGE][1] = -max_float # Max
            
        if type(times) is list or type(times) is ArrayType:  
            outfile.variables['time'][:] = times    #Store time relative
            
        if verbose:
            print '------------------------------------------------'
            print 'Statistics:'
            print '    t in [%f, %f], len(t) == %d'\
                  %(min(times.flat), max(times.flat), len(times.flat))

        
    def store_triangulation(self,
                            outfile,
                            points_utm,
                            volumes,
                            elevation, zone=None, new_origin=None, 
                            points_georeference=None, verbose=False):
        """
        
        new_origin - qa georeference that the points can be set to. (Maybe
        do this before calling this function.)
        
        points_utm - currently a list or array of the points in UTM.
        points_georeference - the georeference of the points_utm
        
        How about passing new_origin and current_origin.
        If you get both, do a convertion from the old to the new.
        
        If you only get new_origin, the points are absolute,
        convert to relative
        
        if you only get the current_origin the points are relative, store
        as relative.
        
        if you get no georefs create a new georef based on the minimums of
        points_utm.  (Another option would be to default to absolute)
        
        Yes, and this is done in another part of the code.
        Probably geospatial.
        
        If you don't supply either geo_refs, then supply a zone. If not
        the default zone will be used.
        
        
        precon
        
        header has been called.
        """
        
        number_of_points = len(points_utm)   
        volumes = array(volumes)  
        points_utm = array(points_utm)

        # given the two geo_refs and the points, do the stuff
        # described in the method header
        # if this is needed else where, pull out as a function
        points_georeference = ensure_geo_reference(points_georeference)
        new_origin = ensure_geo_reference(new_origin)
        if new_origin is None and points_georeference is not None:
            points = points_utm
            geo_ref = points_georeference
        else:
            if new_origin is None:
                new_origin = Geo_reference(zone,min(points_utm[:,0]),
                                           min(points_utm[:,1]))
            points = new_origin.change_points_geo_ref(points_utm,
                                                      points_georeference)
            geo_ref = new_origin

        # At this stage I need a georef and points
        # the points are relative to the georef
        geo_ref.write_NetCDF(outfile)
    
        # This will put the geo ref in the middle
        #geo_ref=Geo_reference(refzone,(max(x)+min(x))/2.0,(max(x)+min(y))/2.)
        
        x =  points[:,0]
        y =  points[:,1]
        z = outfile.variables['z'][:]
    
        if verbose:
            print '------------------------------------------------'
            print 'More Statistics:'
            print '  Extent (/lon):'
            print '    x in [%f, %f], len(lat) == %d'\
                  %(min(x), max(x),
                    len(x))
            print '    y in [%f, %f], len(lon) == %d'\
                  %(min(y), max(y),
                    len(y))
            print '    z in [%f, %f], len(z) == %d'\
                  %(min(elevation), max(elevation),
                    len(elevation))
            print 'geo_ref: ',geo_ref
            print '------------------------------------------------'
            
        #z = resize(bath_grid,outfile.variables['z'][:].shape)
        outfile.variables['x'][:] = points[:,0] #- geo_ref.get_xllcorner()
        outfile.variables['y'][:] = points[:,1] #- geo_ref.get_yllcorner()
        outfile.variables['z'][:] = elevation
        outfile.variables['elevation'][:] = elevation  #FIXME HACK
        outfile.variables['volumes'][:] = volumes.astype(Int32) #On Opteron 64

        q = 'elevation'
        # This updates the _range values
        outfile.variables[q+Write_sww.RANGE][0] = min(elevation)
        outfile.variables[q+Write_sww.RANGE][1] = max(elevation)


    def store_quantities(self, outfile, precision=Float,
                         slice_index=None, time=None,
                         verbose=False, **quant):
        """
        Write the quantity info.

        **quant is extra keyword arguments passed in. These must be
          the sww quantities, currently; stage, xmomentum, ymomentum.
        
        if the time array is already been built, use the slice_index
        to specify the index.
        
        Otherwise, use time to increase the time dimension

        Maybe make this general, but the viewer assumes these quantities,
        so maybe we don't want it general - unless the viewer is general
        
        precon
        triangulation and
        header have been called.
        """

        if time is not None:
            file_time = outfile.variables['time']
            slice_index = len(file_time)
            file_time[slice_index] = time    

        # write the conserved quantities from Domain.
        # Typically stage,  xmomentum, ymomentum
        # other quantities will be ignored, silently.
        for q in Write_sww.sww_quantities:
            if not quant.has_key(q):
                msg = 'SWW file can not write quantity %s' %q
                raise NewQuantity, msg
            else:
                q_values = quant[q]
                outfile.variables[q][slice_index] = q_values.astype(precision)

                # This updates the _range values
                q_range = outfile.variables[q+Write_sww.RANGE][:]
                q_values_min = min(q_values)
                if q_values_min < q_range[0]:
                    outfile.variables[q+Write_sww.RANGE][0] = q_values_min
                q_values_max = max(q_values)
                if q_values_max > q_range[1]:
                    outfile.variables[q+Write_sww.RANGE][1] = q_values_max

    def verbose_quantities(self, outfile):
        print '------------------------------------------------'
        print 'More Statistics:'
        for q in Write_sww.sww_quantities:
            print '  %s in [%f, %f]' %(q,
                                       outfile.variables[q+Write_sww.RANGE][0],
                                       outfile.variables[q+Write_sww.RANGE][1])
        print '------------------------------------------------'


        
def obsolete_write_sww_time_slices(outfile, has, uas, vas, elevation,
                         mean_stage=0, zscale=1,
                         verbose=False):    
    #Time stepping
    stage = outfile.variables['stage']
    xmomentum = outfile.variables['xmomentum']
    ymomentum = outfile.variables['ymomentum']

    n = len(has)
    j=0
    for ha, ua, va in map(None, has, uas, vas):
        if verbose and j%((n+10)/10)==0: print '  Doing %d of %d' %(j, n)
        w = zscale*ha + mean_stage
        stage[j] = w
        h = w - elevation
        xmomentum[j] = ua*h
        ymomentum[j] = -1*va*h  #  -1 since in mux files south is positive.
        j += 1
    
def urs2txt(basename_in, location_index=None):
    """
    Not finished or tested
    """
    
    files_in = [basename_in + WAVEHEIGHT_MUX_LABEL,
                basename_in + EAST_VELOCITY_LABEL,
                basename_in + NORTH_VELOCITY_LABEL]
    quantities = ['HA','UA','VA']

    d = ","
    
    # instanciate urs_points of the three mux files.
    mux = {}
    for quantity, file in map(None, quantities, files_in):
        mux[quantity] = Urs_points(file)
        
    # Could check that the depth is the same. (hashing)

    # handle to a mux file to do depth stuff
    a_mux = mux[quantities[0]]
    
    # Convert to utm
    latitudes = a_mux.lonlatdep[:,1]
    longitudes = a_mux.lonlatdep[:,0]
    points_utm, zone = convert_from_latlon_to_utm( \
        latitudes=latitudes, longitudes=longitudes)
    #print "points_utm", points_utm
    #print "zone", zone
    depths = a_mux.lonlatdep[:,2]  #
    
    fid = open(basename_in + '.txt', 'w')

    fid.write("zone: " + str(zone) + "\n")

    if location_index is not None:
        #Title
        li = location_index
        fid.write('location_index'+d+'lat'+d+ 'long' +d+ 'Easting' +d+ \
                  'Northing' + "\n")
        fid.write(str(li) +d+ str(latitudes[li])+d+ \
              str(longitudes[li]) +d+ str(points_utm[li][0]) +d+ \
              str(points_utm[li][01]) + "\n")

    # the non-time dependent stuff
    #Title
    fid.write('location_index'+d+'lat'+d+ 'long' +d+ 'Easting' +d+ \
                  'Northing' +d+ 'depth m' + "\n")
    i = 0
    for depth, point_utm, lat, long in map(None, depths,
                                               points_utm, latitudes,
                                               longitudes):
        
        fid.write(str(i) +d+ str(lat)+d+ str(long) +d+ str(point_utm[0]) +d+ \
                  str(point_utm[01]) +d+ str(depth) + "\n")
        i +=1
    #Time dependent
    if location_index is not None:
        time_step = a_mux.time_step
        i = 0
        #Title
        fid.write('time' +d+ 'HA depth m'+d+ \
                 'UA momentum East x m/sec' +d+ 'VA momentum North y m/sec' \
                      + "\n")
        for HA, UA, VA in map(None, mux['HA'], mux['UA'], mux['VA']):
            fid.write(str(i*time_step) +d+ str(HA[location_index])+d+ \
                      str(UA[location_index]) +d+ str(VA[location_index]) \
                      + "\n")
            
            i +=1
    
class Urs_points:
    """
    Read the info in URS mux files.

    for the quantities heres a correlation between the file names and
    what they mean;
    z-mux is height above sea level, m
    e-mux is velocity is Eastern direction, m/s
    n-mux is velocity is Northern direction, m/s   
    """
    def __init__(self,urs_file):
        self.iterated = False
        columns = 3 # long, lat , depth
        mux_file = open(urs_file, 'rb')
        
        # Number of points/stations
        (self.points_num,)= unpack('i',mux_file.read(4))
        
        # nt, int - Number of time steps
        (self.time_step_count,)= unpack('i',mux_file.read(4))
        #print "self.time_step_count", self.time_step_count 
        #dt, float - time step, seconds
        (self.time_step,) = unpack('f', mux_file.read(4))
        #print "self.time_step", self.time_step
        msg = "Bad data in the urs file."
        if self.points_num < 0:
            mux_file.close()
            raise ANUGAError, msg
        if self.time_step_count < 0:
            mux_file.close()
            raise ANUGAError, msg
        if self.time_step < 0:
            mux_file.close()
            raise ANUGAError, msg

        # the depth is in meters, and it is the distance from the ocean
        # to the sea bottom.
        lonlatdep = p_array.array('f')
        lonlatdep.read(mux_file, columns * self.points_num)
        lonlatdep = array(lonlatdep, typecode=Float)    
        lonlatdep = reshape(lonlatdep, (self.points_num, columns))
        #print 'lonlatdep',lonlatdep
        self.lonlatdep = lonlatdep
        
        self.mux_file = mux_file
        # check this array

    def __iter__(self):
        """
        iterate over quantity data which is with respect to time.

        Note: You can only interate once over an object
        
        returns quantity infomation for each time slice 
        """
        msg =  "You can only interate once over a urs file."
        assert not self.iterated, msg
        self.iter_time_step = 0
        self.iterated = True
        return self
    
    def next(self):
        if self.time_step_count == self.iter_time_step:
            self.close()
            raise StopIteration
        #Read in a time slice  from mux file  
        hz_p_array = p_array.array('f')
        hz_p_array.read(self.mux_file, self.points_num)
        hz_p = array(hz_p_array, typecode=Float)
        self.iter_time_step += 1
        
        return hz_p

    def close(self):
        self.mux_file.close()
        
    #### END URS UNGRIDDED 2 SWW ###

        
def start_screen_catcher(dir_name, myid='', numprocs='', extra_info='',
                         print_to_screen=False, verbose=False):
    """
    Used to store screen output and errors to file, if run on multiple 
    processes eachprocessor will have its own output and error file.
    
    extra_info - is used as a string that can identify outputs with another 
    string eg. '_other'
    """
    import sys
    dir_name = dir_name
    if access(dir_name,W_OK) == 0:
        if verbose: print 'Make directory %s' %dir_name
        if verbose: print "myid", myid
        mkdir (dir_name,0777)
    if myid <>'':
        myid = '_'+str(myid)
    if numprocs <>'':
        numprocs = '_'+str(numprocs)
    if extra_info <>'':
        extra_info = '_'+str(extra_info)
    screen_output_name = dir_name + "screen_output%s%s%s.txt" %(myid,
                                                                numprocs,
                                                                extra_info)
    screen_error_name = dir_name + "screen_error%s%s%s.txt" %(myid,
                                                              numprocs,
                                                              extra_info)
    print screen_output_name
    #used to catch screen output to file
    sys.stdout = Screen_Catcher(screen_output_name)
    sys.stderr = Screen_Catcher(screen_error_name)

class Screen_Catcher:
    """this simply catches the screen output and stores it to file defined by
    start_screen_catcher (above)
    """
    
    def __init__(self, filename):
        self.filename = filename
 
        if exists(self.filename)is True:
            print'Old existing file "%s" has been deleted' %(self.filename)
            remove(self.filename)

    def write(self, stuff):
        fid = open(self.filename, 'a')
        fid.write(stuff)
#        if print_to_screen: print stuff

def copy_code_files(dir_name, filename1, filename2):
    """Copies "filename1" and "filename2" to "dir_name". Very useful for 
    information management 
    filename1 and filename2 are both absolute pathnames    
    """

    if access(dir_name,F_OK) == 0:
        print 'Make directory %s' %dir_name
        mkdir (dir_name,0777)
    shutil.copy(filename1, dir_name + sep + basename(filename1))
    shutil.copy(filename2, dir_name + sep + basename(filename2))
#    copy (__file__, project.output_run_time_dir + basename(__file__))
    print 'Files %s and %s copied' %(filename1, filename2)

def get_data_from_file(filename,separator_value = ','):
    """ 
    Read in data information from file and 
    
    Returns: 
        header_fields, a string? of the first line separated 
        by the 'separator_value'
        
        data, a array (N data columns X M lines) in the file 
        excluding the header
        
    NOTE: wont deal with columns with different lenghts and there must be
    no blank lines at the end.
    """
    
    fid = open(filename)
    lines = fid.readlines()
    
    fid.close()
    
    header_line = lines[0]
    header_fields = header_line.split(separator_value)

    #array to store data, number in there is to allow float...
    #i'm sure there is a better way!
    data=array([],typecode=Float)
    data=resize(data,((len(lines)-1),len(header_fields)))
#    print 'number of fields',range(len(header_fields))
#    print 'number of lines',len(lines), shape(data)
#    print'data',data[1,1],header_line

    array_number = 0
    line_number = 1
    while line_number < (len(lines)):
        for i in range(len(header_fields)): 
            #this get line below the header, explaining the +1
            #and also the line_number can be used as the array index
            fields = lines[line_number].split(separator_value)
            #assign to array
            data[array_number,i] = float(fields[i])
            
        line_number = line_number +1
        array_number = array_number +1
        
    return header_fields, data

def store_parameters(verbose=False,**kwargs):
    """
    Store "kwargs" into a temp csv file, if "completed" is a kwargs csv file is
    kwargs[file_name] else it is kwargs[output_dir] + details_temp.csv
    
    Must have a file_name keyword arg, this is what is writing to.
    might be a better way to do this using CSV module Writer and writeDict
    
    writes file to "output_dir" unless "completed" is in kwargs, then
    it writes to "file_name" kwargs

    """
    import types
#    import os
    
    # Check that kwargs is a dictionary
    if type(kwargs) != types.DictType:
        raise TypeError
    
    #is completed is kwargs?
    try:
        kwargs['completed']
        completed=True
    except:
        completed=False
 
    #get file name and removes from dict and assert that a file_name exists
    if completed:
        try:
            file = str(kwargs['file_name'])
        except:
            raise 'kwargs must have file_name'
    else:
        #write temp file in output directory
        try:
            file = str(kwargs['output_dir'])+'detail_temp.csv'
        except:
            raise 'kwargs must have output_dir'
        
    #extracts the header info and the new line info
    line=''
    header=''
    count=0
    keys = kwargs.keys()
    keys.sort()
    
    #used the sorted keys to create the header and line data
    for k in keys:
#        print "%s = %s" %(k, kwargs[k]) 
        header = header+str(k)
        line = line+str(kwargs[k])
        count+=1
        if count <len(kwargs):
            header = header+','
            line = line+','
    header+='\n'
    line+='\n'

    # checks the header info, if the same, then write, if not create a new file
    #try to open!
    try:
        fid = open(file,"r")
        file_header=fid.readline()
        fid.close()
        if verbose: print 'read file header %s' %file_header
        
    except:
        msg = 'try to create new file',file
        if verbose: print msg
        #tries to open file, maybe directory is bad
        try:
            fid = open(file,"w")
            fid.write(header)
            fid.close()
            file_header=header
        except:
            msg = 'cannot create new file',file
            raise msg
            
    #if header is same or this is a new file
    if file_header==str(header):
        fid=open(file,"a")
        #write new line
        fid.write(line)
        fid.close()
    else:
        #backup plan,
        # if header is different and has completed will append info to 
        #end of details_temp.cvs file in output directory
        file = str(kwargs['output_dir'])+'detail_temp.csv'
        fid=open(file,"a")
        fid.write(header)
        fid.write(line)
        fid.close()
        if verbose: print 'file',file_header.strip('\n')
        if verbose: print 'head',header.strip('\n')
        if file_header.strip('\n')==str(header): print 'they equal'
        msg = 'WARNING: File header does not match input info, the input variables have changed, suggest to change file name'
        print msg





def get_maximum_inundation_elevation(filename,
                                     polygon=None,
                                     time_interval=None,
                                     verbose=False):
    
    """Return highest elevation where depth > 0
    
    Usage:
    max_runup = get_maximum_inundation_elevation(filename,
                                                 polygon=None,
                                                 time_interval=None,
                                                 verbose=False)

    filename is a NetCDF sww file containing ANUGA model output.    
    Optional arguments polygon and time_interval restricts the maximum
    runup calculation
    to a points that lie within the specified polygon and time interval.

    If no inundation is found within polygon and time_interval the return value
    is None signifying "No Runup" or "Everything is dry".

    See general function get_maximum_inundation_data for details.
    
    """
    
    runup, _ = get_maximum_inundation_data(filename,
                                           polygon=polygon,
                                           time_interval=time_interval,
                                           verbose=verbose)
    return runup




def get_maximum_inundation_location(filename,
                                    polygon=None,
                                    time_interval=None,
                                    verbose=False):
    """Return location of highest elevation where h > 0
    
    
    Usage:
    max_runup_location = get_maximum_inundation_location(filename,
                                                         polygon=None,
                                                         time_interval=None,
                                                         verbose=False)

    filename is a NetCDF sww file containing ANUGA model output.
    Optional arguments polygon and time_interval restricts the maximum
    runup calculation
    to a points that lie within the specified polygon and time interval.

    If no inundation is found within polygon and time_interval the return value
    is None signifying "No Runup" or "Everything is dry".

    See general function get_maximum_inundation_data for details.
    """
    
    _, max_loc = get_maximum_inundation_data(filename,
                                             polygon=polygon,
                                             time_interval=time_interval,
                                             verbose=verbose)
    return max_loc
    


def get_maximum_inundation_data(filename, polygon=None, time_interval=None,
                                use_centroid_values=False,
                                verbose=False):
    """Compute maximum run up height from sww file.


    Usage:
    runup, location = get_maximum_inundation_data(filename,
                                                  polygon=None,
                                                  time_interval=None,
                                                  verbose=False)
    

    Algorithm is as in get_maximum_inundation_elevation from
    shallow_water_domain
    except that this function works with the sww file and computes the maximal
    runup height over multiple timesteps. 
    
    Optional arguments polygon and time_interval restricts the
    maximum runup calculation
    to a points that lie within the specified polygon and time interval.
    Polygon is
    assumed to be in (absolute) UTM coordinates in the same zone as domain.

    If no inundation is found within polygon and time_interval the return value
    is None signifying "No Runup" or "Everything is dry".
    """

    # We are using nodal values here as that is what is stored in sww files.

    # Water depth below which it is considered to be 0 in the model
    # FIXME (Ole): Allow this to be specified as a keyword argument as well

    from anuga.utilities.polygon import inside_polygon    
    from anuga.config import minimum_allowed_height
    from Scientific.IO.NetCDF import NetCDFFile

    dir, base = os.path.split(filename)
            
    iterate_over = get_all_swwfiles(dir,base)
    
    # Read sww file
    if verbose: 
        print 'Reading from %s' %filename
        # FIXME: Use general swwstats (when done)
    
    maximal_runup = None
    maximal_runup_location = None
    
    for file, swwfile in enumerate (iterate_over):
        
        # Read sww file
        filename = join(dir,swwfile+'.sww')
        
        if verbose: 
            print 'Reading from %s' %filename
            # FIXME: Use general swwstats (when done)
                
        fid = NetCDFFile(filename)
    
        # Get geo_reference
        # sww files don't have to have a geo_ref
        try:
            geo_reference = Geo_reference(NetCDFObject=fid)
        except AttributeError, e:
            geo_reference = Geo_reference() # Default georef object
            
        xllcorner = geo_reference.get_xllcorner()
        yllcorner = geo_reference.get_yllcorner()
        zone = geo_reference.get_zone()
        
        # Get extent
        volumes = fid.variables['volumes'][:]    
        x = fid.variables['x'][:] + xllcorner
        y = fid.variables['y'][:] + yllcorner
    
    
        # Get the relevant quantities
        elevation = fid.variables['elevation'][:] 
        stage = fid.variables['stage'][:]
    
    
        # Here's where one could convert nodal information to centroid
        # information
        # but is probably something we need to write in C.
        # Here's a Python thought which is NOT finished!!!
        if use_centroid_values is True:
            x = get_centroid_values(x, volumes)
            y = get_centroid_values(y, volumes)    
            elevation = get_centroid_values(elevation, volumes)    
    
    
        # Spatial restriction
        if polygon is not None:
            msg = 'polygon must be a sequence of points.'
            assert len(polygon[0]) == 2, msg
            # FIXME (Ole): Make a generic polygon input check in polygon.py
            # and call it here
            
            points = concatenate((x[:,NewAxis], y[:,NewAxis]), axis=1)
    
            point_indices = inside_polygon(points, polygon)
    
            # Restrict quantities to polygon
            elevation = take(elevation, point_indices)
            stage = take(stage, point_indices, axis=1)
    
            # Get info for location of maximal runup
            points_in_polygon = take(points, point_indices)
            x = points_in_polygon[:,0]
            y = points_in_polygon[:,1]        
        else:
            # Take all points
            point_indices = arange(len(x))
            
    
        # Temporal restriction
        time = fid.variables['time'][:]
        all_timeindices = arange(len(time))        
        if time_interval is not None:
            
            msg = 'time_interval must be a sequence of length 2.'
            assert len(time_interval) == 2, msg
            msg = 'time_interval %s must not be decreasing.' %(time_interval)
            assert time_interval[1] >= time_interval[0], msg
            
            msg = 'Specified time interval [%.8f:%.8f]' %tuple(time_interval)
            msg += ' must does not match model time interval: [%.8f, %.8f]\n'\
                   %(time[0], time[-1])
            if time_interval[1] < time[0]: raise ValueError(msg)
            if time_interval[0] > time[-1]: raise ValueError(msg)
    
            # Take time indices corresponding to interval (& is bitwise AND)
            timesteps = compress((time_interval[0] <= time) & (time <= time_interval[1]),
                                 all_timeindices)
    
    
            msg = 'time_interval %s did not include any model timesteps.' %(time_interval)        
            assert not alltrue(timesteps == 0), msg
    
    
        else:
            # Take them all
            timesteps = all_timeindices
        
    
        fid.close()
    
        # Compute maximal runup for each timestep
        #maximal_runup = None
        #maximal_runup_location = None
        #maximal_runups = [None]
        #maximal_runup_locations = [None]
        
        for i in timesteps:
            if use_centroid_values is True:
                stage_i = get_centroid_values(stage[i,:], volumes)   
            else:
                stage_i = stage[i,:]
                
            depth = stage_i  - elevation 
        
            # Get wet nodes i.e. nodes with depth>0 within given region and timesteps
            wet_nodes = compress(depth > minimum_allowed_height, arange(len(depth)))
    
            if alltrue(wet_nodes == 0):
                runup = None
            else:    
                # Find maximum elevation among wet nodes
                wet_elevation = take(elevation, wet_nodes)
    
                runup_index = argmax(wet_elevation)
                runup = max(wet_elevation)
                assert wet_elevation[runup_index] == runup # Must always be True
            if runup > maximal_runup:
                maximal_runup = runup      # This works even if maximal_runups is None
                #print "NEW RUNUP",runup
    
                # Record location
                wet_x = take(x, wet_nodes)
                wet_y = take(y, wet_nodes)            
                maximal_runup_location = [wet_x[runup_index], wet_y[runup_index]]
    
    #print 'maximal_runup, maximal_runup_location',maximal_runup, maximal_runup_location
    return maximal_runup, maximal_runup_location

def get_all_swwfiles(look_in_dir='',base_name='',verbose=False):
    '''
    Finds all the sww files in a "look_in_dir" which contains a "base_name". 
    will accept base_name with or without the extension ".sww"
    
    Returns: a list of strings
        
    Usage:     iterate_over = get_all_swwfiles(dir, name)
    then
               for swwfile in iterate_over:
                   do stuff
                   
    Check "export_grids" and "get_maximum_inundation_data" for examples
    '''
    
    #plus tests the extension
    name, extension = os.path.splitext(base_name)

    if extension <>'' and extension <> '.sww':
        msg = msg='file %s %s must be an NetCDF sww file!'%(base_name,extension)
        raise IOError, msg

    if look_in_dir == "":
        look_in_dir = "." # Unix compatibility
    
    dir_ls = os.listdir(look_in_dir)
    #print 'dir_ls',dir_ls, base
    iterate_over = [x[:-4] for x in dir_ls if name in x and x[-4:] == '.sww']
    if len(iterate_over) == 0:
        msg = 'No files of the base name %s'\
              %(name)
        raise IOError, msg
    if verbose: print 'iterate over %s' %(iterate_over)

    return iterate_over

def get_all_files_with_extension(look_in_dir='',base_name='',extension='.sww',verbose=False):
    '''
    Finds all the sww files in a "look_in_dir" which contains a "base_name". 
    
    
    Returns: a list of strings
        
    Usage:     iterate_over = get_all_swwfiles(dir, name)
    then
               for swwfile in iterate_over:
                   do stuff
                   
    Check "export_grids" and "get_maximum_inundation_data" for examples
    '''
    
    #plus tests the extension
    name, ext = os.path.splitext(base_name)
#    print 'look_in_dir',look_in_dir

    if ext <>'' and ext <> extension:
        msg = msg='base_name %s must be an file with %s extension!'%(base_name,extension)
        raise IOError, msg

    if look_in_dir == "":
        look_in_dir = "." # Unix compatibility
#    print 'look_in_dir',look_in_dir, getcwd()
    dir_ls = os.listdir(look_in_dir)
    #print 'dir_ls',dir_ls, base_name
    iterate_over = [x[:-4] for x in dir_ls if name in x and x[-4:] == extension]
    if len(iterate_over) == 0:
        msg = 'No files of the base name %s in %s'\
              %(name, look_in_dir)
        raise IOError, msg
    if verbose: print 'iterate over %s' %(iterate_over)

    return iterate_over

def get_all_directories_with_name(look_in_dir='',base_name='',verbose=False):
    '''
    Finds all the sww files in a "look_in_dir" which contains a "base_name". 
    
    
    Returns: a list of strings
        
    Usage:     iterate_over = get_all_swwfiles(dir, name)
    then
               for swwfile in iterate_over:
                   do stuff
                   
    Check "export_grids" and "get_maximum_inundation_data" for examples
    '''
    
    #plus tests the extension

    if look_in_dir == "":
        look_in_dir = "." # Unix compatibility
#    print 'look_in_dir',look_in_dir
    dir_ls = os.listdir(look_in_dir)
#    print 'dir_ls',dir_ls
    iterate_over = [x for x in dir_ls if base_name in x]
    if len(iterate_over) == 0:
        msg = 'No files of the base name %s'\
              %(name)
        raise IOError, msg
    if verbose: print 'iterate over %s' %(iterate_over)

    return iterate_over


#-------------------------------------------------------------
if __name__ == "__main__":
    #setting umask from config to force permissions for all files and directories
    # created to the same. (it was noticed the "mpirun" doesn't honour the umask
    # set in your .bashrc etc file)
    from config import umask
    import os 
    os.umask(umask)
