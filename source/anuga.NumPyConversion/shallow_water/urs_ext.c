/*
gcc -fPIC -c urs_ext.c -I/usr/include/python2.5 -o urs_ext.o -Wall -O
gcc -shared urs_ext.o  -o urs_ext.so
*/

#include "Python.h"
#include "Numeric/arrayobject.h"
#include "structure.h"
#include "math.h"
#include <stdio.h>
#include <float.h>
#include <time.h>

#define MAX_FILE_NAME_LENGTH 128
#define NODATA 99.0
#define EPSILON  0.00001

#define DEBUG 0

#define POFFSET 5 //Number of site_params

static int *fros=NULL; 
static int *lros=NULL;
static struct tgsrwg* mytgs0=NULL;

static long numDataMax=0;


/*The MUX file format 


 */



/////////////////////////////////////////////////////////////////////////
//Auxiliary functions
void fillDataArray(int ista, int total_number_of_stations, int nt, int ig, int *nst, 
                   int *nft, float *data, int *istart_p, 
		   int *istop_p, float *muxData)
{
    int it, last_it, jsta;
    long int offset=0;


    last_it = -1;
    /* Make arrays of starting and finishing time steps for the tide gauges */
    /* and fill them from the file */

    /* Update start and stop timesteps for this gauge */
    if (nst[ista]!= -1)
    {
        if(*istart_p == -1)
        {
            *istart_p = nst[ista];
        }
        else
        {
            *istart_p = ((nst[ista] < *istart_p) ? nst[ista] : *istart_p);
        }
    }
    
    if (nft[ista] != -1)
    {
        if (*istop_p == -1)
        {
            *istop_p = nft[ista];
        }
        else
        {
            *istop_p = ((nft[ista] < *istop_p) ? nft[ista] : *istop_p);
        }
    }     
    
    if (ig == -1 || nst[ista] == -1) /* currently ig==-1 => nst[ista]==-1 */
    {
        /* gauge never started recording, or was outside of all grids, 
        fill array with 0 */
        for(it = 0; it < nt; it++)
        {
            data[it] = 0.0;
        }
    }   
    else
    {
        for(it = 0; it < nt; it++)
        {
            last_it = it;
            /* skip t record of data block */
            offset++;
            /* skip records from earlier tide gauges */
            for(jsta = 0; jsta < ista; jsta++)
                if(it + 1 >= nst[jsta] && it + 1 <= nft[jsta])
                    offset++;

            /* deal with the tide gauge at hand */
            if(it + 1 >= nst[ista] && it + 1 <= nft[ista])
            {
                /* gauge is recording at this time */
                memcpy(data + it, muxData + offset, sizeof(float));
                offset++;
            }
            else if (it + 1 < nst[ista])
            {
                /* gauge has not yet started recording */
                data[it] = 0.0;
            }   
            else
                /* gauge has finished recording */
            {
                data[it] = NODATA;
                break;
            }

            /* skip records from later tide gauges */
            for(jsta = ista + 1; jsta < total_number_of_stations; jsta++)
                if(it + 1 >= nst[jsta] && it+1 <= nft[jsta])
                    offset++;
        }

        if(last_it < nt - 1)
            /* the loop was exited early because the gauge had 
            finished recording */
            for(it = last_it+1; it < nt; it++)
                data[it] = NODATA;
    }
} 


char isdata(float x)
{
    if(x < NODATA + EPSILON && NODATA < x + EPSILON)
    {
      return 0;
    }
    else
    {
      return 1;  
    }
}


long getNumData(const int *fros, const int *lros, const int total_number_of_stations)
/* calculates the number of data in the data block of a mux file */
/* based on the first and last recorded output steps for each gauge */ 
{
    int ista, last_output_step;
    long numData = 0;

    last_output_step = 0;   
    for(ista = 0; ista < total_number_of_stations; ista++)
        if(*(fros + ista) != -1)
        {
            numData += *(lros + ista) - *(fros + ista) + 1;
            last_output_step = (last_output_step < *(lros+ista) ? 
                            *(lros+ista):last_output_step);
        }   
        numData += last_output_step*total_number_of_stations; /* these are the t records */
        return numData;
}

/////////////////////////////////////////////////////////////////////////
//Internal Functions
int _read_mux2_headers(int numSrc, 
		       char **muxFileNameArray, 
		       int* total_number_of_stations,
		       int* number_of_time_steps,
		       double* delta_t,
		       //long* numDataMax,
		       int verbose)
{
    FILE *fp;
    int numsta, i, j;
    struct tgsrwg *mytgs=0;
    char *muxFileName;                                                                  
    char susMuxFileName;
    long numData;

    /* Allocate space for the names and the weights and pointers to the data*/

    /* Check that the input files have mux2 extension*/
    susMuxFileName = 0;
    for(i = 0; i < numSrc; i++)
    { 
        muxFileName = muxFileNameArray[i];
        if(!susMuxFileName && strcmp(muxFileName + strlen(muxFileName) - 4, 
                     "mux2") != 0)
        {
            susMuxFileName = 1;
            break;
        }
    }

    if(susMuxFileName)
    {
        printf("\n**************************************************************************\n");
        printf("   WARNING: This program operates only on multiplexed files in mux2 format\n"); 
        printf("   At least one input file name does not end with mux2\n");
        printf("   Check your results carefully!\n");
        printf("**************************************************************************\n\n");
    }   

    if (verbose)
    {
        printf("Reading mux header information\n");
    }

    // Loop over all sources, read headers and check compatibility
    for (i = 0; i < numSrc; i++)
    {
        muxFileName = muxFileNameArray[i];

        // Open the mux file
        if((fp = fopen(muxFileName, "r")) == NULL)
        {
            fprintf(stderr, "cannot open file %s\n", muxFileName);
            return 0;  
        }
        
        if (!i)
        {
            fread(total_number_of_stations, sizeof(int), 1, fp);
        
            fros = (int*) malloc(*total_number_of_stations*numSrc*sizeof(int)); 
            lros = (int*) malloc(*total_number_of_stations*numSrc*sizeof(int));
      
            mytgs0 = (struct tgsrwg*) malloc(*total_number_of_stations*sizeof(struct tgsrwg));
            mytgs = (struct tgsrwg*) malloc(*total_number_of_stations*sizeof(struct tgsrwg));

            fread(mytgs0, *total_number_of_stations*sizeof(struct tgsrwg), 1, fp);
        }
        else
        {
	    // Check that the mux files are compatible
            fread(&numsta, sizeof(int), 1, fp);
            if(numsta != *total_number_of_stations)
            {
                fprintf(stderr,"%s has different number of stations to %s\n", 
                muxFileName, 
                muxFileNameArray[0]);
                fclose(fp);
                return 0;   
            }

            fread(mytgs, numsta*sizeof(struct tgsrwg), 1, fp); 
            
            for (j = 0; j < numsta; j++)
            {
                if (mytgs[j].dt != mytgs0[j].dt)
                {
                    fprintf(stderr, "%s has different sampling rate to %s\n", 
                    muxFileName, 
                    muxFileNameArray[0]);
                    fclose(fp);
                    return 0;            
                }   
                if (mytgs[j].nt != mytgs0[j].nt)
                {
                    fprintf(stderr, "%s has different series length to %s\n", 
                    muxFileName, 
                    muxFileNameArray[0]);
                    fclose(fp);
                    return 0;            
                }

                if (mytgs[j].nt != mytgs0[0].nt)
                {
                    printf("Station 0 has different series length to Station %d\n", j); 
                }
            }
        }

        /* Read the start and stop times for this source */
        fread(fros + i*(*total_number_of_stations), 
	      *total_number_of_stations*sizeof(int), 1, fp);
        fread(lros + i*(*total_number_of_stations), 
	      *total_number_of_stations*sizeof(int), 1, fp);

        /* Compute the size of the data block for this source */
        numData = getNumData(fros + i*(*total_number_of_stations), 
			     lros + i*(*total_number_of_stations), 
			     (*total_number_of_stations));

        /* Sanity check */
        if (numData < 0)
        {
            fprintf(stderr,"Size of data block appears to be negative!\n");
            return 0;        
        }

        if (numDataMax < numData)
        {
            numDataMax = numData;
        }

        fclose(fp);          
    }

    
    // Store time resolution and number of timesteps    
    // These are the same for all stations as tested above, so 
    // we take the first one.
    *delta_t = (double)mytgs0[0].dt;
    *number_of_time_steps = mytgs0[0].nt;

    free(mytgs);

    return 1;
}


float** _read_mux2(int numSrc, 
                   char **muxFileNameArray, 
                   float *weights, 
                   double *params, 
                   int *number_of_stations,
                   long *permutation,
                   int verbose)
{
    FILE *fp;
    int total_number_of_stations, i, isrc, ista, k;
    char *muxFileName;
    int istart=-1, istop=-1;
    int number_of_selected_stations;
    float *muxData=NULL; // Suppress warning
    long numData;

    int len_sts_data;
    float **sts_data;
    float *temp_sts_data;

    long int offset;

    int number_of_time_steps, N;
    double delta_t;
    
    // Shorthands pointing to memory blocks for each source
    int *fros_per_source=NULL;     
    int *lros_per_source=NULL;         
    
    _read_mux2_headers(numSrc, 
                       muxFileNameArray, 
                       &total_number_of_stations,
                       &number_of_time_steps,
                       &delta_t,
                       verbose);

    // Apply rule that an empty permutation file means 'take all stations'
    // We could change this later by passing in None instead of the empty 
    // permutation.
    number_of_selected_stations = *number_of_stations;  
    if (number_of_selected_stations == 0)
    {
        number_of_selected_stations = total_number_of_stations;  
    
        // Return possibly updated number of stations
        *number_of_stations = total_number_of_stations;     
      
        // Create the Identity permutation vector
        permutation = (long *) malloc(number_of_selected_stations*sizeof(long));
        for (i = 0; i < number_of_selected_stations; i++)
        {
            permutation[i] = (long) i;  
        }
    }
   
    // The params array is used only for passing data back to Python.
    params[0] = (double) number_of_selected_stations;
    params[1] = (double) delta_t;
    params[2] = (double) number_of_time_steps;
    
   
    
    // Make array(s) to hold demuxed data for stations given in the 
    // permutation file 
    sts_data = (float**) malloc(number_of_selected_stations*sizeof(float*));
    if (sts_data == NULL)
    {
        printf("ERROR: Memory for sts_data could not be allocated.\n");
        return NULL;
    }

    // For each selected station, allocate space for its data
    len_sts_data = number_of_time_steps + POFFSET; // Max length of each timeseries?
    for (i = 0; i < number_of_selected_stations; i++)
    {
        // Initialise sts_data to zero
        sts_data[i] = (float*) calloc(len_sts_data, sizeof(float));
        if (sts_data[i] == NULL)
        {
            printf("ERROR: Memory for sts_data could not be allocated.\n");
            return NULL;
        }
    }

    temp_sts_data = (float*) calloc(len_sts_data, sizeof(float));

    muxData = (float*) malloc(numDataMax*sizeof(float));
    
    // Loop over all sources
    for (isrc = 0; isrc < numSrc; isrc++)
    {
    
        // Shorthands to local memory
        fros_per_source = (int*) fros + isrc*total_number_of_stations; 
        lros_per_source = (int*) lros + isrc*total_number_of_stations; 	    
	    
    
        // Read in data block from mux2 file
        muxFileName = muxFileNameArray[isrc];
        if((fp = fopen(muxFileName, "r")) == NULL)
        {
            fprintf(stderr, "cannot open file %s\n", muxFileName);
            return NULL;                    
        }

        if (verbose){
            printf("Reading mux file %s\n", muxFileName);
        }

        offset = sizeof(int) + total_number_of_stations*(sizeof(struct tgsrwg) + 2*sizeof(int));
        fseek(fp, offset, 0);

        numData = getNumData(fros_per_source, 
			     lros_per_source, 
			     total_number_of_stations);
			     
        fread(muxData, numData*sizeof(float), 1, fp); 
        fclose(fp);

        // loop over stations present in the permutation array 
        //     use ista with mux data
        //     use i with the processed data to be returned         
        for(i = 0; i < number_of_selected_stations; i++)
        {               
    
            ista = (int) permutation[i]; // Get global index into mux data  
        
            // fill the data0 array from the mux file, and weight it
            fillDataArray(ista, 
                          total_number_of_stations, 
			  number_of_time_steps,
                          mytgs0[ista].ig, // Grid number (if -1 fill with zeros)
                          fros_per_source, 
                          lros_per_source, 
                          temp_sts_data, 
                          &istart, 
                          &istop, 
                          muxData);

            // Weight appropriately and add
            for(k = 0; k < mytgs0[ista].nt; k++)
            {
                if((isdata(sts_data[i][k])) && isdata(temp_sts_data[k]))
                {
                    sts_data[i][k] += temp_sts_data[k] * weights[isrc];
                }
                else
                {
                    sts_data[i][k] = NODATA;
                }
            }
	    
	    // Update metadata (e.g. start time and end time)
	    N = number_of_time_steps;
	    
	    if (isrc == 0) {
	        // Assign values for first source
	        sts_data[i][N] = (float)mytgs0[ista].geolat;
		sts_data[i][N+1] = (float)mytgs0[ista].geolon;
		sts_data[i][N+2] = (float)mytgs0[ista].z;
		sts_data[i][N+3] = (float)fros_per_source[ista];
		sts_data[i][N+4] = (float)lros_per_source[ista];
	    } else {
	        // Update first and last timesteps for subsequent sources
	        if (sts_data[i][N+3] > (float)fros_per_source[ista]) {		
		    if (verbose) {
		        printf("Adjusting start time for station %d and source %d",
			       ista, isrc);
			printf(" from %f to %f\n", 
			       sts_data[i][N+3], 
			       (float) fros_per_source[ista]);  
		    }
		    sts_data[i][N+3] = (float) fros_per_source[ista];
		}
		
	        if (sts_data[i][N+4] < (float) lros_per_source[ista]) {		
		    if (verbose) {
		        printf("Adjusting end time for station %d and source %d",
			       ista, isrc);
			printf(" from %f to %f\n", 
			       sts_data[i][N+4], 
			       (float) lros_per_source[ista]);  
		    }
		    sts_data[i][N+4] = (float) lros_per_source[ista];
		}		
	    }
        }
    }

    free(muxData);
    free(temp_sts_data);
    free(fros);
    free(lros);
    free(mytgs0);

    return sts_data;
}

/////////////////////////////////////////////////////////////////////////
//Python gateways
PyObject *read_mux2(PyObject *self, PyObject *args)
{
    /*Read in mux 2 file

    Python call:
    read_mux2(numSrc,filenames,weights,file_params,permutation,verbose)

    NOTE:
    A Python int is equivalent to a C long 
    (this becomes really important on 64 bit architectures)
    
    A Python double corresponds to a C double
    */
    
    PyObject *filenames;
    PyArrayObject *pyweights;
    PyArrayObject *file_params;
    PyArrayObject *permutation;  // Ordering of selected stations    
    PyArrayObject *pydata;
    PyObject *fname;

    char **muxFileNameArray;
    float **cdata;
    float *weights;
    int dimensions[2];
    int numSrc;
    int verbose;
    int total_number_of_stations;
    int number_of_selected_stations;    
    int nt;
    double dt;
    int i;
    int j;
    int start_tstep;
    int finish_tstep;
    int it;
    int time;
    int num_ts;
    
    // Convert Python arguments to C
    if (!PyArg_ParseTuple(args, "iOOOOi",
              &numSrc, &filenames, &pyweights, &file_params, 
              &permutation, &verbose)) 
    {
            PyErr_SetString(PyExc_RuntimeError, 
                "Input arguments to read_mux2 failed");
            return NULL;
    }

    if(!PyList_Check(filenames)) 
    {
        PyErr_SetString(PyExc_TypeError, "get_first_elem expects a list");
        return NULL;
    }

    if(PyList_Size(filenames) == 0)
    {
        PyErr_SetString(PyExc_ValueError, "empty lists not allowed");
        return NULL;
    }

    if (pyweights->nd != 1 || pyweights->descr->type_num != PyArray_DOUBLE) 
    {
        PyErr_SetString(PyExc_ValueError,
            "pyweights must be one-dimensional and of type double");
        return NULL; 
    }

    if(PyList_Size(filenames) != pyweights->dimensions[0])
    {
        PyErr_SetString(PyExc_ValueError, 
            "Must specify one weight for each filename");
        return NULL;
    }

    muxFileNameArray = (char**)malloc(numSrc*sizeof(char*));
    if (muxFileNameArray == NULL) 
    {
        PyErr_SetString(PyExc_ValueError, 
			"ERROR: Memory for muxFileNameArray could not be allocated.");
        return NULL;
    }

    for (i = 0; i < numSrc; i++)
    {

        fname = PyList_GetItem(filenames, i);
        if (!fname) 
        {
            PyErr_SetString(PyExc_ValueError, "filename not a string");
            return NULL;
        }       

        muxFileNameArray[i] = PyString_AsString(fname);
        if (muxFileNameArray[i] == NULL) 
        {
            PyErr_SetString(PyExc_ValueError, 
          "ERROR: Memory for muxFileNameArray could not be allocated.\n");
            return NULL;
        }
    }

    if (file_params->nd != 1 || file_params->descr->type_num != PyArray_DOUBLE) 
    {
        PyErr_SetString(PyExc_ValueError,
          "file_params must be one-dimensional and of type double");
        return NULL; 
    }


    // Create array for weights which are passed to read_mux2
    weights = (float*) malloc(numSrc*sizeof(float));
    for (i = 0; i < numSrc; i++)
    {
        weights[i] = (float)(*(double*)(pyweights->data + i*pyweights->strides[0]));
    }
    
    // Desired number of stations
    number_of_selected_stations = (int) permutation->dimensions[0];

    // Read in mux2 data from file
    cdata = _read_mux2(numSrc, 
                       muxFileNameArray, 
                       weights, 
                       (double*)file_params->data,
                       &number_of_selected_stations, 
                       (long*) permutation->data,
                       verbose);

    if (!cdata) 
    {
        PyErr_SetString(PyExc_ValueError, "No STS_DATA returned");
        return NULL;
    }       
               
               
    // Allocate space for return vector
    total_number_of_stations = (int)*(double*)(file_params->data + 0*file_params->strides[0]);
    dt = *(double*)(file_params->data + 1*file_params->strides[0]);
    nt = (int)*(double*)(file_params->data + 2*file_params->strides[0]);

    
    // Find min and max start times of all gauges
    start_tstep = nt + 1;
    finish_tstep = -1;
    for (i = 0; i < number_of_selected_stations; i++)
    {
        //printf("cdata[%d] start = %f\n", i, (double) cdata[i][nt+3]);
        // printf("cdata[%d] finish = %f\n", i, (double) cdata[i][nt+4]);   
    
        if ((int)cdata[i][nt + 3] < start_tstep)
        {
            start_tstep = (int)cdata[i][nt + 3];
        }
        if ((int)cdata[i][nt + 4] > finish_tstep)
        {
            finish_tstep = (int)cdata[i][nt + 4]; 
        }
    }

    if ((start_tstep > nt) | (finish_tstep < 0))
    {
        printf("ERROR: Gauge data has incorrect start and finish times:\n");
        printf("   start_tstep = %d, max_number_of_steps = %d\n", 
               start_tstep, nt);
        printf("   finish_tstep = %d, min_number_of_steps = %d\n", 
               finish_tstep, 0);    
           
        PyErr_SetString(PyExc_ValueError, "Incorrect start and finish times");  
        return NULL;
    }

    if (start_tstep >= finish_tstep)
    {
        PyErr_SetString(PyExc_ValueError,
                    "ERROR: Gauge data has non-postive_length");
        return NULL;
    }

    num_ts = finish_tstep - start_tstep + 1;
    dimensions[0] = number_of_selected_stations;
    dimensions[1] = num_ts + POFFSET;
    
    pydata = (PyArrayObject*) PyArray_FromDims(2, dimensions, PyArray_DOUBLE);
    if(pydata == NULL)
    {
        PyErr_SetString(PyExc_ValueError, 
          "ERROR: Memory for pydata array could not be allocated.");
        return NULL;
    }

    
    // Each gauge begins and ends recording at different times. When a gauge is
    // not recording but at least one other gauge is. 
    // Pad the non-recording gauge array with zeros.
    for (i = 0; i < number_of_selected_stations; i++)
    {
        time = 0;
        for (it = 0; it < finish_tstep; it++)
        {
            if ((it + 1 >= start_tstep) && (it + 1 <= finish_tstep))
            {
                if (it + 1 > (int)cdata[i][nt + 4])
                {
                    // This gauge has stopped recording but others are 
                    // still recording
                    *(double*)(pydata->data + i*pydata->strides[0] 
			       + time*pydata->strides[1]) = 
		      0.0;
                }
                else
                {
                    *(double*)(pydata->data + i*pydata->strides[0] 
                                    + time*pydata->strides[1]) = 
                        cdata[i][it];
                }
                time++;
            }
        }
        // Pass back lat,lon,elevation
        for (j = 0; j < POFFSET; j++)
        {
            *(double*)(pydata->data + i*pydata->strides[0] 
                                + (num_ts + j)*pydata->strides[1]) = 
                    cdata[i][nt + j];
        }
    }

    free(weights);
    
    // Free filename array, but not independent Python strings
    // FIXME(Ole): Do we need to update a reference counter in this case?
    free(muxFileNameArray);
    
    for (i = 0; i < number_of_selected_stations; ++i)
    {
        free(cdata[i]);
    }
    free(cdata);

    return  PyArray_Return(pydata);
}

//-------------------------------
// Method table for python module
//-------------------------------
static struct PyMethodDef MethodTable[] = {
    {"read_mux2", read_mux2, METH_VARARGS, "Print out"},
    {NULL, NULL}
};

// Module initialisation
void initurs_ext(void){
    Py_InitModule("urs_ext", MethodTable);

    import_array(); // Necessary for handling of NumPY structures
}