"""Class Domain -
2D triangular domains for finite-volume computations of
the advection equation.

This module contains a specialisation of class Domain from module domain.py
consisting of methods specific to the advection equantion

The equation is

  u_t + (v_1 u)_x + (v_2 u)_y = 0

There is only one conserved quantity, the stage u

The advection equation is a very simple specialisation of the generic
domain and may serve as an instructive example or a test of other
components such as visualisation.

Ole Nielsen, Stephen Roberts, Duncan Gray, Christopher Zoppou
Geoscience Australia, 2004
"""


#import logging, logging.config
#logger = logging.getLogger('advection')
#logger.setLevel(logging.WARNING)
#
#try:
#    logging.config.fileConfig('log.ini')
#except:
#    pass


from anuga.abstract_2d_finite_volumes.domain import *
Generic_domain = Domain # Rename

class Domain(Generic_domain):

    def __init__(self,
                 coordinates,
                 vertices,
                 boundary = None,
                 tagged_elements = None,
                 geo_reference = None,
                 use_inscribed_circle=False,
                 velocity = None,
                 full_send_dict=None,
                 ghost_recv_dict=None,
                 processor=0,
                 numproc=1
                 ):

        conserved_quantities = ['stage']
        other_quantities = []
        Generic_domain.__init__(self,
                                source=coordinates,
                                triangles=vertices,
                                boundary=boundary,
                                conserved_quantities=conserved_quantities,
                                other_quantities=other_quantities,
                                tagged_elements=tagged_elements,
                                geo_reference=geo_reference,
                                use_inscribed_circle=use_inscribed_circle,
                                full_send_dict=full_send_dict,
                                ghost_recv_dict=ghost_recv_dict,
                                processor=processor,
                                numproc=numproc)

        import Numeric
        if velocity is None:
            self.velocity = Numeric.array([1,0],'d')
        else:
            self.velocity = Numeric.array(velocity,'d')

        #Only first is implemented for advection
        self.set_default_order(1)
        self.set_beta(1.0)
        
        self.smooth = True

    def check_integrity(self):
        Generic_domain.check_integrity(self)

        msg = 'Conserved quantity must be "stage"'
        assert self.conserved_quantities[0] == 'stage', msg


    def distribute_to_vertices_and_edges(self):
        """Extrapolate conserved quantities from centroid to
        vertices and edge-midpoints for each volume

        Default implementation is straight first order,
        i.e. constant values throughout each element and
        no reference to non-conserved quantities.
        """

        for name in self.conserved_quantities:
            Q = self.quantities[name]
            if self._order_ == 1:
                Q.extrapolate_first_order()
            elif self._order_ == 2:
                Q.extrapolate_second_order_and_limit_by_edge()
                #Q.limit()
            else:
                raise 'Unknown order'
            #Q.interpolate_from_vertices_to_edges()




    def flux_function(self, normal, ql, qr, zl=None, zr=None):
        """Compute outward flux as inner product between velocity
        vector v=(v_1, v_2) and normal vector n.

        if <n,v> > 0 flux direction is outward bound and its magnitude is
        determined by the quantity inside volume: ql.
        Otherwise it is inbound and magnitude is determined by the
        quantity outside the volume: qr.
        """

        v1 = self.velocity[0]
        v2 = self.velocity[1]


        normal_velocity = v1*normal[0] + v2*normal[1]

        if normal_velocity < 0:
            flux = qr * normal_velocity
        else:
            flux = ql * normal_velocity

        max_speed = abs(normal_velocity)
        return flux, max_speed



    def compute_fluxes(self):
        """Compute all fluxes and the timestep suitable for all volumes
        in domain.

        Compute total flux for each conserved quantity using "flux_function"

        Fluxes across each edge are scaled by edgelengths and summed up
        Resulting flux is then scaled by area and stored in
        domain.explicit_update

        The maximal allowable speed computed by the flux_function
        for each volume
        is converted to a timestep that must not be exceeded. The minimum of
        those is computed as the next overall timestep.

        Post conditions:
        domain.explicit_update is reset to computed flux values
        domain.timestep is set to the largest step satisfying all volumes.
        """

        import sys
        from Numeric import zeros, Float
        from anuga.config import max_timestep


        huge_timestep = float(sys.maxint)
        Stage = self.quantities['stage']

        """
        print "======================================"
        print "BEFORE compute_fluxes"
        print "stage_update",Stage.explicit_update
        print "stage_edge",Stage.edge_values
        print "stage_bdry",Stage.boundary_values
        print "neighbours",self.neighbours
        print "neighbour_edges",self.neighbour_edges
        print "normals",self.normals
        print "areas",self.areas
        print "radii",self.radii
        print "edgelengths",self.edgelengths
        print "tri_full_flag",self.tri_full_flag
        print "huge_timestep",huge_timestep
        print "max_timestep",max_timestep
        print "velocity",self.velocity
        """

        import advection_ext		
        self.flux_timestep = advection_ext.compute_fluxes(self, Stage, huge_timestep, max_timestep)



##     def evolve(self,
##                yieldstep = None,
##                finaltime = None,
##                duration = None,
##                skip_initial_step = False):

##         """Specialisation of basic evolve method from parent class
##         """

##         #Call basic machinery from parent class
##         for t in Generic_domain.evolve(self,
##                                        yieldstep=yieldstep,
##                                        finaltime=finaltime,
##                                        duration=duration,
##                                        skip_initial_step=skip_initial_step):

##             #Pass control on to outer loop for more specific actions
##             yield(t)




    def compute_fluxes_python(self):
        """Compute all fluxes and the timestep suitable for all volumes
        in domain.

        Compute total flux for each conserved quantity using "flux_function"

        Fluxes across each edge are scaled by edgelengths and summed up
        Resulting flux is then scaled by area and stored in
        domain.explicit_update

        The maximal allowable speed computed by the flux_function
        for each volume
        is converted to a timestep that must not be exceeded. The minimum of
        those is computed as the next overall timestep.

        Post conditions:
        domain.explicit_update is reset to computed flux values
        domain.timestep is set to the largest step satisfying all volumes.
        """

        import sys
        from Numeric import zeros, Float
        from anuga.config import max_timestep

        N = len(self)

        neighbours = self.neighbours
        neighbour_edges = self.neighbour_edges
        normals = self.normals

        areas = self.areas
        radii = self.radii
        edgelengths = self.edgelengths

        timestep = max_timestep #FIXME: Get rid of this

        #Shortcuts
        Stage = self.quantities['stage']

        #Arrays
        stage = Stage.edge_values

        stage_bdry = Stage.boundary_values

        flux = zeros(1, Float) #Work array for summing up fluxes

        #Loop
        for k in range(N):
            optimal_timestep = float(sys.maxint)

            flux[:] = 0.  #Reset work array
            for i in range(3):
                #Quantities inside volume facing neighbour i
                ql = stage[k, i]

                #Quantities at neighbour on nearest face
                n = neighbours[k,i]
                if n < 0:
                    m = -n-1 #Convert neg flag to index
                    qr = stage_bdry[m]
                else:
                    m = neighbour_edges[k,i]
                    qr = stage[n, m]


                #Outward pointing normal vector
                normal = normals[k, 2*i:2*i+2]

                #Flux computation using provided function
                edgeflux, max_speed = self.flux_function(normal, ql, qr)
                flux -= edgeflux * edgelengths[k,i]

                #Update optimal_timestep
                if  self.tri_full_flag[k] == 1 :
                    try:
                        optimal_timestep = min(optimal_timestep, radii[k]/max_speed)
                    except ZeroDivisionError:
                        pass

            #Normalise by area and store for when all conserved
            #quantities get updated
            flux /= areas[k]
            Stage.explicit_update[k] = flux[0]

            timestep = min(timestep, optimal_timestep)

        self.timestep = timestep
