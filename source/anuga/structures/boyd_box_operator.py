import anuga
import math
import types

class Boyd_box_operator(anuga.Structure_operator):
    """Culvert flow - transfer water from one rectangular box to another.
    Sets up the geometry of problem
    
    This is the base class for culverts. Inherit from this class (and overwrite
    compute_discharge method for specific subclasses)
    
    Input: Two points, pipe_size (either diameter or width, height), 
    mannings_rougness,
    """ 

    def __init__(self,
                 domain,
                 end_point0, 
                 end_point1,
                 losses,
                 width,
                 height=None,
                 apron=None,
                 manning=0.013,
                 enquiry_gap=0.2,
                 use_momentum_jet=True,
                 use_velocity_head=True,
                 description=None,
                 verbose=False):
                     
        anuga.Structure_operator.__init__(self,
                                          domain,
                                          end_point0, 
                                          end_point1,
                                          width,
                                          height,
                                          apron,
                                          manning,
                                          enquiry_gap,                                                       
                                          description,
                                          verbose)            
        
        
        if type(losses) == types.DictType:
            self.sum_loss = sum(losses.values())
        elif type(losses) == types.ListType:
            self.sum_loss = sum(losses)
        else:
            self.sum_loss = losses
        
        self.use_momentum_jet = use_momentum_jet
        self.use_velocity_head = use_velocity_head
        
        self.culvert_length = self.get_culvert_length()
        self.culvert_width = self.get_culvert_width()
        self.culvert_height = self.get_culvert_height()

        self.max_velocity = 10.0
        self.log_filename = None

        self.inlets = self.get_inlets()


        # Stats
        
        self.discharge = 0.0
        self.velocity = 0.0
        
        
    def __call__(self):
        
        timestep = self.domain.get_timestep()
        
        self.__determine_inflow_outflow()
        
        Q, barrel_speed, outlet_depth = self.__discharge_routine()

        old_inflow_height = self.inflow.get_average_height()
        old_inflow_xmom = self.inflow.get_average_xmom()
        old_inflow_ymom = self.inflow.get_average_ymom()
            
        if old_inflow_height > 0.0 :
                Qstar = Q/old_inflow_height
        else:
                Qstar = 0.0

        factor = 1.0/(1.0 + Qstar*timestep/self.inflow.get_area())

        new_inflow_height = old_inflow_height*factor
        new_inflow_xmom = old_inflow_xmom*factor
        new_inflow_ymom = old_inflow_ymom*factor
            

        self.inflow.set_heights(new_inflow_height)

        #inflow.set_xmoms(Q/inflow.get_area())
        #inflow.set_ymoms(0.0)


        self.inflow.set_xmoms(new_inflow_xmom)
        self.inflow.set_ymoms(new_inflow_ymom)


        loss = (old_inflow_height - new_inflow_height)*self.inflow.get_area()

            
        # set outflow
        if old_inflow_height > 0.0 :
                timestep_star = timestep*new_inflow_height/old_inflow_height
        else:
            timestep_star = 0.0

            
        outflow_extra_height = Q*timestep_star/self.outflow.get_area()
        outflow_direction = - self.outflow.outward_culvert_vector
        outflow_extra_momentum = outflow_extra_height*barrel_speed*outflow_direction
            

        gain = outflow_extra_height*self.outflow.get_area()
            
        #print Q, Q*timestep, barrel_speed, outlet_depth, Qstar, factor, timestep_star
        #print '  ', loss, gain

        # Stats
        self.discharge  = Q#outflow_extra_height*self.outflow.get_area()/timestep
        self.velocity = barrel_speed#self.discharge/outlet_depth/self.width

        new_outflow_height = self.outflow.get_average_height() + outflow_extra_height

        if self.use_momentum_jet :
            # FIXME (SR) Review momentum to account for possible hydraulic jumps at outlet
            #new_outflow_xmom = outflow.get_average_xmom() + outflow_extra_momentum[0]
            #new_outflow_ymom = outflow.get_average_ymom() + outflow_extra_momentum[1]

            new_outflow_xmom = barrel_speed*new_outflow_height*outflow_direction[0]
            new_outflow_ymom = barrel_speed*new_outflow_height*outflow_direction[1]

        else:
            #new_outflow_xmom = outflow.get_average_xmom()
            #new_outflow_ymom = outflow.get_average_ymom()

            new_outflow_xmom = 0.0
            new_outflow_ymom = 0.0


        self.outflow.set_heights(new_outflow_height)
        self.outflow.set_xmoms(new_outflow_xmom)
        self.outflow.set_ymoms(new_outflow_ymom)


    def __determine_inflow_outflow(self):
        # Determine flow direction based on total energy difference

        if self.use_velocity_head:
            self.delta_total_energy = self.inlets[0].get_enquiry_total_energy() - self.inlets[1].get_enquiry_total_energy()
        else:
            self.delta_total_energy = self.inlets[0].get_enquiry_stage() - self.inlets[1].get_enquiry_stage()


        self.inflow  = self.inlets[0]
        self.outflow = self.inlets[1]
        

        if self.delta_total_energy < 0:
            self.inflow  = self.inlets[1]
            self.outflow = self.inlets[0]
            self.delta_total_energy = -self.delta_total_energy

    
    def __discharge_routine(self):

        local_debug ='false'
        
        if self.inflow.get_enquiry_height() > 0.01: #this value was 0.01:
            if local_debug =='true':
                anuga.log.critical('Specific E & Deltat Tot E = %s, %s'
                             % (str(self.inflow.get_enquiry_specific_energy()),
                                str(self.delta_total_energy)))
                anuga.log.critical('culvert type = %s' % str(culvert_type))
            # Water has risen above inlet

            if self.log_filename is not None:
                s = 'Specific energy  = %f m' % self.inflow.get_enquiry_specific_energy()
                log_to_file(self.log_filename, s)

            msg = 'Specific energy at inlet is negative'
            assert self.inflow.get_enquiry_specific_energy() >= 0.0, msg

            if self.use_velocity_head :
                self.driving_energy = self.inflow.get_enquiry_specific_energy()
            else:
                self.driving_energy = self.inflow.get_enquiry_height()

            height = self.culvert_height
            width = self.culvert_width
            flow_width = self.culvert_width
            # intially assume the culvert flow is controlled by the inlet
            # check unsubmerged and submerged condition and use Min Q
            # but ensure the correct flow area and wetted perimeter are used
            Q_inlet_unsubmerged = 0.544*anuga.g**0.5*width*self.driving_energy**1.50 # Flow based on Inlet Ctrl Inlet Unsubmerged
            Q_inlet_submerged = 0.702*anuga.g**0.5*width*height**0.89*self.driving_energy**0.61  # Flow based on Inlet Ctrl Inlet Submerged

            # FIXME(Ole): Are these functions really for inlet control?
            if Q_inlet_unsubmerged < Q_inlet_submerged:
                Q = Q_inlet_unsubmerged
                dcrit = (Q**2/anuga.g/width**2)**0.333333
                if dcrit > height:
                    dcrit = height
                    flow_area = width*dcrit
                    perimeter= 2.0*(width+dcrit)
                else: # dcrit < height
                    flow_area = width*dcrit
                    perimeter= 2.0*dcrit+width
                outlet_culvert_depth = dcrit
                case = 'Inlet unsubmerged Box Acts as Weir'
            else: # Inlet Submerged but check internal culvert flow depth
                Q = Q_inlet_submerged
                dcrit = (Q**2/anuga.g/width**2)**0.333333
                if dcrit > height:
                    dcrit = height
                    flow_area = width*dcrit
                    perimeter= 2.0*(width+dcrit)
                else: # dcrit < height
                    flow_area = width*dcrit
                    perimeter= 2.0*dcrit+width
                outlet_culvert_depth = dcrit
                case = 'Inlet submerged Box Acts as Orifice'

            dcrit = (Q**2/anuga.g/width**2)**0.333333
            # May not need this .... check if same is done above
            outlet_culvert_depth = dcrit
            if outlet_culvert_depth > height:
                outlet_culvert_depth = height  # Once again the pipe is flowing full not partfull
                flow_area = width*height  # Cross sectional area of flow in the culvert
                perimeter = 2*(width+height)
                case = 'Inlet CTRL Outlet unsubmerged PIPE PART FULL'
            else:
                flow_area = width * outlet_culvert_depth
                perimeter = width+2*outlet_culvert_depth
                case = 'INLET CTRL Culvert is open channel flow we will for now assume critical depth'
            # Initial Estimate of Flow for Outlet Control using energy slope 
            #( may need to include Culvert Bed Slope Comparison)
            hyd_rad = flow_area/perimeter
            culvert_velocity = math.sqrt(self.delta_total_energy/((self.sum_loss/2/anuga.g)+(self.manning**2*self.culvert_length)/hyd_rad**1.33333))
            Q_outlet_tailwater = flow_area * culvert_velocity
            
            
            if self.delta_total_energy < self.driving_energy:
                # Calculate flows for outlet control

                # Determine the depth at the outlet relative to the depth of flow in the Culvert
                if self.outflow.get_enquiry_height() > height:        # The Outlet is Submerged
                    outlet_culvert_depth=height
                    flow_area=width*height       # Cross sectional area of flow in the culvert
                    perimeter=2.0*(width+height)
                    case = 'Outlet submerged'
                else:   # Here really should use the Culvert Slope to calculate Actual Culvert Depth & Velocity
                    dcrit = (Q**2/anuga.g/width**2)**0.333333
                    outlet_culvert_depth=dcrit   # For purpose of calculation assume the outlet depth = Critical Depth
                    if outlet_culvert_depth > height:
                        outlet_culvert_depth=height
                        flow_area=width*height
                        perimeter=2.0*(width+height)
                        case = 'Outlet is Flowing Full'
                    else:
                        flow_area=width*outlet_culvert_depth
                        perimeter=(width+2.0*outlet_culvert_depth)
                        case = 'Outlet is open channel flow'

                hyd_rad = flow_area/perimeter

                if self.log_filename is not None:
                    s = 'hydraulic radius at outlet = %f' % hyd_rad
                    log_to_file(self.log_filename, s)

                # Final Outlet control velocity using tail water
                culvert_velocity = math.sqrt(self.delta_total_energy/((self.sum_loss/2/anuga.g)+(self.manning**2*self.culvert_length)/hyd_rad**1.33333))
                Q_outlet_tailwater = flow_area * culvert_velocity

                if self.log_filename is not None:
                    s = 'Q_outlet_tailwater = %.6f' % Q_outlet_tailwater
                    log_to_file(self.log_filename, s)
                Q = min(Q, Q_outlet_tailwater)
            else:
                pass
                #FIXME(Ole): What about inlet control?

            culv_froude=math.sqrt(Q**2*flow_width/(anuga.g*flow_area**3))
            if local_debug =='true':
                anuga.log.critical('FLOW AREA = %s' % str(flow_area))
                anuga.log.critical('PERIMETER = %s' % str(perimeter))
                anuga.log.critical('Q final = %s' % str(Q))
                anuga.log.critical('FROUDE = %s' % str(culv_froude))

            # Determine momentum at the outlet
            barrel_velocity = Q/(flow_area + anuga.velocity_protection/flow_area)

        # END CODE BLOCK for DEPTH  > Required depth for CULVERT Flow

        else: # self.inflow.get_enquiry_height() < 0.01:
            Q = barrel_velocity = outlet_culvert_depth = 0.0

        # Temporary flow limit
        if barrel_velocity > self.max_velocity:
            barrel_velocity = self.max_velocity
            Q = flow_area * barrel_velocity

        return Q, barrel_velocity, outlet_culvert_depth
        
        
        