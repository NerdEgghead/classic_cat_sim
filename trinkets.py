"""Code for modeling non-static trinkets in feral DPS simulation."""

import numpy as np


class Trinket():

    """Keeps track of activation times and cooldowns for an equipped trinket,
    updates Player and Simulation parameters when the trinket is active, and
    determines when procs or trinket activations occur."""

    def __init__(
        self, stat_name, stat_increment, proc_name, proc_duration, cooldown
    ):
        """Initialize a generic trinket with key parameters.

        Arguments:
            stat_name (str or list): Name of the Player attribute that will be
                modified by the trinket activation. Must be a valid attribute
                of the Player class that can be modified. The one exception is
                haste_rating, which is separately handled by the Simulation
                object when updating timesteps for the sim. A list of strings
                can be provided instead, in which case every stat in the list
                will be modified during the trinket activation.
            stat_increment (float or np.ndarray): Amount by which the Player
                attribute is changed when the trinket is active. If multiple
                stat names are specified, then this must be a numpy array of
                equal length to the number of stat names.
            proc_name (str): Name of the buff that is applied when the trinket
                is active. Used for combat logging.
            proc_duration (int): Duration of the buff, in seconds.
            cooldown (int): Internal cooldown before the trinket can be
                activated again, either via player use or procs.
        """
        self.stat_name = stat_name
        self.stat_increment = stat_increment
        self.proc_name = proc_name
        self.proc_duration = proc_duration
        self.cooldown = cooldown
        self.reset()

    def reset(self):
        """Set trinket to fresh inactive state with no cooldown remaining."""
        self.activation_time = -np.inf
        self.active = False
        self.can_proc = True
        self.num_procs = 0
        self.uptime = 0.0
        self.last_update = 0.0

    def modify_stat(self, time, player, sim, increment):
        """Change a player stat when a trinket is activated or deactivated.

        Arguments:
            time (float): Simulation time, in seconds, of activation.
            player (ccs.Player): Player object whose attributes will be
                modified.
            sim (ccs.Simulation): Simulation object controlling the fight
                execution.
            increment (float or np.ndarray): Quantity to add to the player's
                existing stat value(s).
        """
        # Convert stat name and stat increment to arrays if they are scalars
        stat_names = np.atleast_1d(self.stat_name)
        increments = np.atleast_1d(increment)

        for index, stat_name in enumerate(stat_names):
            self._modify_stat(time, player, sim, stat_name, increments[index])

    @staticmethod
    def _modify_stat(time, player, sim, stat_name, increment):
        """Contains the actual stat modification functionality for a single
        stat. Called by the wrapper function, which handles potentially
        iterating through multiple stats to be modified."""
        # Haste procs get handled separately from other raw stat buffs
        if stat_name == 'haste':
            if increment > 0:
                sim.update_swing_times(time, sim.swing_timer / (1 + increment))
            else:
                sim.update_swing_times(time, sim.swing_timer * (1 - increment))
        else:
            old_value = getattr(player, stat_name)
            setattr(player, stat_name, old_value + increment)

            # Recalculate damage parameters when player stats change
            player.calc_damage_params(**sim.params)

    def activate(self, time, player, sim):
        """Activate the trinket buff upon player usage or passive proc.

        Arguments:
            time (float): Simulation time, in seconds, of activation.
            player (ccs.Player): Player object whose attributes will be
                modified by the trinket proc.
            sim (ccs.Simulation): Simulation object controlling the fight
                execution.

        Returns:
            damage_done (float): Any instant damage that is dealt when the
                trinket is activated. Defaults to 0 for standard trinkets, but
                custom subclasses can implement fixed damage procs that would
                be calculated in this method.
        """
        self.activation_time = time
        self.deactivation_time = time + self.proc_duration
        self.modify_stat(time, player, sim, self.stat_increment)
        sim.proc_end_times.append(self.deactivation_time)

        # In the case of a second trinket being used, the proc end time can
        # sometimes be earlier than that of the first trinket, so the list of
        # end times needs to be sorted.
        sim.proc_end_times.sort()

        # Mark trinket as active
        self.active = True
        self.can_proc = False
        self.num_procs += 1

        # Log if requested
        if sim.log:
            sim.combat_log.append(sim.gen_log(time, self.proc_name, 'applied'))

        # Return default damage dealt of 0
        return 0.0

    def deactivate(self, player, sim, time=None):
        """Deactivate the trinket buff when the duration has expired.

        Arguments:
            player (ccs.Player): Player object whose attributes will be
                restored to their original values.
            sim (ccs.Simulation): Simulation object controlling the fight
                execution.
            time (float): Time at which the trinket is deactivated. Defaults to
                the stored time for automatic deactivation.
        """
        if time is None:
            time = self.deactivation_time

        self.modify_stat(time, player, sim, -self.stat_increment)
        self.active = False

        if sim.log:
            sim.combat_log.append(
                sim.gen_log(time, self.proc_name, 'falls off')
            )

    def update(self, time, player, sim, allow_activation=True):
        """Check for a trinket activation or deactivation at the specified
        simulation time, and perform associated bookkeeping.

        Arguments:
            time (float): Simulation time, in seconds.
            player (ccs.Player): Player object whose attributes will be
                modified by the trinket proc.
            sim (ccs.Simulation): Simulation object controlling the fight
                execution.
            allow_activation (bool): Allow the trinket to be activated
                automatically if the appropriate conditions are met. Defaults
                True, but can be set False if the user wants to control
                trinket activations manually.

        Returns:
            damage_done (float): Any instant damage that is dealt if the
                trinket is activated at the specified time. Defaults to 0 for
                standard trinkets, but custom subclasses can implement fixed
                damage procs that would be returned on each update.
        """
        # Update average proc uptime value
        if time > self.last_update:
            dt = time - self.last_update
            self.uptime = (
                (self.uptime * self.last_update + dt * self.active) / time
            )
            self.last_update = time

        # First check if an existing buff has fallen off
        if self.active and (time > self.deactivation_time - 1e-9):
            self.deactivate(player, sim)

        # Then check whether the trinket is off CD and can now proc
        if (not self.can_proc
                and (time - self.activation_time > self.cooldown - 1e-9)):
            self.can_proc = True

        # Now decide whether a proc actually happens
        if allow_activation and self.apply_proc():
            return self.activate(time, player, sim)

        # Return default damage dealt of 0
        return 0.0

    def apply_proc(self):
        """Determine whether or not the trinket is activated at the current
        time. This method must be implemented by Trinket subclasses.

        Returns:
            proc_applied (bool): Whether or not the activation occurs.
        """
        return NotImplementedError(
            'Logic for trinket activation must be implemented by Trinket '
            'subclasses.'
        )


class ActivatedTrinket(Trinket):
    """Models an on-use trinket that is activated on cooldown as often as
    possible."""

    def __init__(
        self, stat_name, stat_increment, proc_name, proc_duration, cooldown,
        delay=0.0
    ):
        """Initialize a generic activated trinket with key parameters.

        Arguments:
            stat_name (str): Name of the Player attribute that will be
                modified by the trinket activation. Must be a valid attribute
                of the Player class that can be modified. The one exception is
                haste_rating, which is separately handled by the Simulation
                object when updating timesteps for the sim.
            stat_increment (float): Amount by which the Player attribute is
                changed when the trinket is active.
            proc_name (str): Name of the buff that is applied when the trinket
                is active. Used for combat logging.
            proc_duration (int): Duration of the buff, in seconds.
            cooldown (int): Internal cooldown before the trinket can be
                activated again.
            delay (float): Optional time delay (in seconds) before the first
                trinket activation in the fight. Can be used to enforce a
                shared cooldown between two activated trinkets, or to delay the
                activation for armor debuffs etc. Defaults to 0.0 .
        """
        self.delay = delay
        Trinket.__init__(
            self, stat_name, stat_increment, proc_name, proc_duration,
            cooldown
        )

    def reset(self):
        """Set trinket to fresh inactive state at the start of a fight."""
        if self.delay:
            # We put in a hack to set the "activation time" such that the
            # trinket is ready after precisely the delay
            self.activation_time = self.delay - self.cooldown
        else:
            # Otherwise, the initial activation time is set infinitely in the
            # past so that the trinket is immediately ready for activation.
            self.activation_time = -np.inf

        self.active = False
        self.can_proc = not self.delay
        self.num_procs = 0
        self.uptime = 0.0
        self.last_update = 0.0

    def apply_proc(self):
        """Determine whether or not the trinket is activated at the current
        time.

        Returns:
            proc_applied (bool): Whether or not the activation occurs.
        """
        # Activated trinkets follow the simple logic of being used as soon as
        # they are available.
        if self.can_proc:
            return True
        return False


class ProcTrinket(Trinket):
    """Models a passive trinket with a specified proc chance on hit or crit."""

    def __init__(
        self, stat_name, stat_increment, proc_name, chance_on_hit,
        proc_duration, cooldown, chance_on_crit=0.0, yellow_chance_on_hit=None
    ):
        """Initialize a generic proc trinket with key parameters.

        Arguments:
            stat_name (str): Name of the Player attribute that will be
                modified by the trinket activation. Must be a valid attribute
                of the Player class that can be modified. The one exception is
                haste_rating, which is separately handled by the Simulation
                object when updating timesteps for the sim.
            stat_increment (float): Amount by which the Player attribute is
                changed when the trinket is active.
            proc_name (str): Name of the buff that is applied when the trinket
                is active. Used for combat logging.
            chance_on_hit (float): Probability of a proc on a successful normal
                hit, between 0 and 1.
            chance_on_crit (float): Probability of a proc on a critical strike,
                between 0 and 1. Defaults to 0.
            yellow_chance_on_hit (float): If supplied, use a separate proc rate
                for special abilities. In this case, chance_on_hit will be
                interpreted as the proc rate for white attacks. Used for ppm
                trinkets where white and yellow proc rates are normalized
                differently.
            proc_duration (int): Duration of the buff, in seconds.
            cooldown (int): Internal cooldown before the trinket can proc
                again.
        """
        Trinket.__init__(
            self, stat_name, stat_increment, proc_name, proc_duration,
            cooldown
        )

        if yellow_chance_on_hit is not None:
            self.rates = {
                'white': chance_on_hit, 'yellow': yellow_chance_on_hit
            }
            self.separate_yellow_procs = True
        else:
            self.chance_on_hit = chance_on_hit
            self.chance_on_crit = chance_on_crit
            self.separate_yellow_procs = False

    def check_for_proc(self, crit, yellow):
        """Perform random roll for a trinket proc upon a successful attack.

        Arguments:
            crit (bool): Whether the attack was a critical strike.
            yellow (bool): Whether the attack was a special ability rather
                than a melee attack.
        """
        if not self.can_proc:
            self.proc_happened = False
            return

        proc_roll = np.random.rand()

        if self.separate_yellow_procs:
            rate = self.rates['yellow'] if yellow else self.rates['white']
        else:
            rate = self.chance_on_crit if crit else self.chance_on_hit

        if proc_roll < rate:
            self.proc_happened = True
        else:
            self.proc_happened = False

    def apply_proc(self):
        """Determine whether or not the trinket is activated at the current
        time. For a proc trinket, it is assumed that a check has already been
        made for the proc when the most recent attack occurred.

        Returns:
            proc_applied (bool): Whether or not the activation occurs.
        """
        if self.can_proc and self.proc_happened:
            self.proc_happened = False
            return True
        return False

    def reset(self):
        """Set trinket to fresh inactive state with no cooldown remaining."""
        Trinket.reset(self)
        self.proc_happened = False


class BadgeOfTheSwarmguard(ProcTrinket):
    """Custom class to handle the unique behavior of stacking Badge procs."""

    def __init__(self, white_chance_on_hit, yellow_chance_on_hit, *args):
        """Initialize a Trinket object modeling Badge. Since Badge is a ppm
        trinket, the user must pre-calculate the proc chances based on the
        swing timer and equipped weapon speed.

        Arguments:
            white_chance_on_hit (float): Probability of a proc on a successful
                normal hit, between 0 and 1.
            yellow_chance_on_hit (float): Separate proc rate for special
                abilities.
        """
        ProcTrinket.__init__(
            self, stat_name='armor_pen', stat_increment=0,
            proc_name='Badge of the Swarmguard', proc_duration=30,
            cooldown=180., chance_on_hit=white_chance_on_hit,
            yellow_chance_on_hit=yellow_chance_on_hit
        )

    def reset(self):
        """Full reset of the trinket at the start of a fight."""
        self.activation_time = -np.inf
        self._reset()
        self.stat_increment = 0
        self.num_procs = 0
        self.uptime = 0.0
        self.last_update = 0.0

    def _reset(self):
        self.active = False
        self.can_proc = False
        self.proc_happened = False
        self.num_stacks = 0
        self.proc_name = 'Badge of the Swarmguard'

    def deactivate(self, player, sim, time=None):
        """Deactivate the trinket buff when the duration has expired.

        Arguments:
            player (tbc_cat_sim.Player): Player object whose attributes will be
                restored to their original values.
            sim (tbc_cat_sim.Simulation): Simulation object controlling the
                fight execution.
            time (float): Time at which the trinket is deactivated. Defaults to
                the stored time for automatic deactivation.
        """
        # Temporarily change the stat increment to the total ArP gained while
        # the trinket was active
        self.stat_increment = 200 * self.num_stacks

        # Reset trinket to inactive state
        self._reset()
        Trinket.deactivate(self, player, sim, time=time)
        self.stat_increment = 0

    def apply_proc(self):
        """Determine whether a new trinket activation takes place, or whether
        a new stack is applied to an existing activation."""
        # If can_proc is True but the stat increment is 0, it means that the
        # last event was a trinket deactivation, so we activate the trinket.
        if (not self.active) and self.can_proc and (self.stat_increment == 0):
            return True

        # Ignore procs when at 6 stacks, and prevent future proc checks
        if self.num_stacks == 6:
            self.can_proc = False
            return False

        return ProcTrinket.apply_proc(self)

    def activate(self, time, player, sim):
        """Activate the trinket when off cooldown. If already active and a
        trinket proc just occurred, then add a new stack of armor pen.

        Arguments:
            time (float): Simulation time, in seconds, of activation.
            player (tbc_cat_sim.Player): Player object whose attributes will be
                modified by the trinket proc.
            sim (tbc_cat_sim.Simulation): Simulation object controlling the
                fight execution.
        """
        if not self.active:
            # Activate the trinket on a fresh use
            Trinket.activate(self, time, player, sim)
            self.can_proc = True
            self.proc_name = 'Insight of the Qiraji'
            self.stat_increment = 200
        else:
            # Apply a new ArP stack. We do this "manually" rather than in the
            # parent method because a new stack doesn't count as an actual
            # activation.
            self.modify_stat(time, player, sim, self.stat_increment)
            self.num_stacks += 1

            # Log if requested
            if sim.log:
                sim.combat_log.append(
                    sim.gen_log(time, self.proc_name, 'applied')
                )

        return 0.0


class InstantDamageProc(ProcTrinket):
    """Custom class to handle instant damage procs such as the Darkmoon Card:
    Maelstrom trinket."""

    def __init__(
        self, chance_on_hit, yellow_chance_on_hit, min_damage, max_damage,
        proc_name, *args, **kwargs
    ):
        """Initialize Trinket object. Since instant damage procs are ppm
        trinkets, the user must pre-calculate the proc chances based on the
        swing timer and equipped weapon speed.

        Arguments:
            chance_on_hit (float): Probability of a proc on a successful
                normal hit, between 0 and 1.
            yellow_chance_on_hit (float): Separate proc rate for special
                abilities.
            min_damage (float): Minimum damage of the proc, including damage
                multipliers from Curse of Elements etc.
            max_damage (float): Maximum damage of the proc, including damage
                multipliers from Curse of Elements etc.
            proc_name (str): Name of the proc ability, used for combat logging.
        """
        ProcTrinket.__init__(
            self, stat_name=None, stat_increment=None, proc_name=proc_name,
            proc_duration=0, cooldown=0., chance_on_hit=chance_on_hit,
            yellow_chance_on_hit=yellow_chance_on_hit
        )
        self.min_damage = min_damage
        self.damage_range = max_damage - min_damage

    def activate(self, time, sim):
        """Deal damage when the trinket procs.

        Arguments:
            time (float): Simulation time, in seconds, of activation.
            sim (ccs.Simulation): Simulation object controlling the fight
                execution.

        Returns:
            damage_done (float): Damage dealt by the proc.
        """
        self.num_procs += 1

        # First roll for miss. Assume 0 spell hit, so miss chance is 17%.
        miss_roll = np.random.rand()

        if miss_roll < 0.17:
            if sim.log:
                sim.combat_log.append(
                    sim.gen_log(time, self.proc_name, 'miss')
                )

            return 0.0

        # Now roll the base damage done by the proc
        base_damage = self.min_damage + np.random.rand() * self.damage_range

        # Now roll for partial resists. Assume that the boss has no spell
        # resistance, so the only source of partials is the level based
        # resistance of 24 for a boss mob. The partial resist table for this
        # condition was taken from this calculator:
        # https://royalgiraffe.github.io/legacy-sim/#/resistances
        resist_roll = np.random.rand()

        if resist_roll < 0.82:
            dmg_done = base_damage
        elif resist_roll < 0.95:
            dmg_done = 0.75 * base_damage
        elif resist_roll < 0.99:
            dmg_done = 0.5 * base_damage
        else:
            dmg_done = 0.25 * base_damage

        if sim.log:
            sim.combat_log.append(
                sim.gen_log(time, self.proc_name, '%d' % dmg_done)
            )

        return dmg_done

    def update(self, time, player, sim):
        """Check if a trinket proc occurred on the player's last attack, and
        perform associated bookkeeping.

        Arguments:
            time (float): Simulation time, in seconds.
            player (tbc_cat_sim.Player): Player object whose attributes can be
                modified by trinket procs. Unused for calculations, but
                required by the Trinket API.
            sim (tbc_cat_sim.Simulation): Simulation object controlling the
                fight execution.

        Returns:
            damage_done (float): Damage dealt by the trinket since the last
                check.
        """
        if self.apply_proc():
            return self.activate(time, sim)

        return 0.0


class HoJ(ProcTrinket):
    """Models the Hand of Justice trinket proc."""

    def __init__(self):
        """Initialize trinket with hardecoded parameters."""
        ProcTrinket.__init__(
            self, stat_name=None, stat_increment=None,
            proc_name='Hand of Justice', chance_on_hit=0.02,
            chance_on_crit=0.02, proc_duration=0.0, cooldown=2.0
        )

    def activate(self, time, player, sim):
        """Reset swing timer on a successful proc.

        Arguments:
            time (float): Simulation time, in seconds, of activation.
            player (ccs.Player): Player object whose attributes will be
                modified by the trinket proc.
            sim (ccs.Simulation): Simulation object controlling the fight
                execution.

        Returns:
            damage_done (float): Any instant damage that is dealt when the
                trinket is activated. Defaults to 0 for standard trinkets, but
                custom subclasses can implement fixed damage procs that would
                be calculated in this method.
        """
        # HoJ procs are processed on the next spell batch after the attack that
        # triggered the proc. Let us assume that the batch timer is randomly
        # distributed relative to the current time. This means that the proc is
        # "registered" between 0-10 ms after self.activation_time. The swing
        # timer reset itself occurs on one full spell batch *after* this. So
        # the reset time = self.activation time + [0-10 ms] + 10 ms.
        swing_reset_time = time + 0.010 * (1 + np.random.rand())
        self.activation_time = swing_reset_time

        # If the reset time occurs before the next scheduled swing, then we
        # calculate swing times starting from that point. If it occurs *after*
        # the next scheduled swing, then we need to prepend that scheduled
        # swing in front of the reset swing schedule.
        next_scheduled_swing = sim.swing_times[0]
        sim.update_swing_times(
            swing_reset_time, sim.swing_timer, first_swing=True
        )

        if swing_reset_time > next_scheduled_swing:
            sim.swing_times = [next_scheduled_swing] + sim.swing_times

        # Final bookkeeping
        if sim.log:
            sim.combat_log.append(sim.gen_log(time, self.proc_name, 'applied'))

        self.num_procs += 1
        sim.can_proc = False
        return 0.0


class RefreshingProcTrinket(ProcTrinket):
    """Handles trinkets that can proc when already active to refresh the buff
    duration."""

    def activate(self, time, player, sim):
        """Activate the trinket buff upon player usage or passive proc.

        Arguments:
            time (float): Simulation time, in seconds, of activation.
            player (tbc_cat_sim.Player): Player object whose attributes will be
                modified by the trinket proc.
            sim (tbc_cat_sim.Simulation): Simulation object controlling the
                fight execution.
        """
        # The only difference between a standard and repeating proc is that
        # we want to make sure that the buff doesn't stack and merely
        # refreshes. This is accomplished by deactivating the previous buff and
        # then re-applying it.
        if self.active:
            self.deactivate(player, sim, time=time)

        return ProcTrinket.activate(self, time, player, sim)


# Library of recognized TBC trinkets and associated parameters
trinket_library = {
    'earthstrike': {
        'type': 'activated',
        'passive_stats': {},
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 280,
            'proc_name': 'Earthstrike',
            'proc_duration': 20,
            'cooldown': 120,
        },
    },
    'slayers': {
        'type': 'activated',
        'passive_stats': {
            'attack_power': 64,
        },
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 260,
            'proc_name': "Slayer's Crest",
            'proc_duration': 20,
            'cooldown': 120,
        },
    },
    'kiss': {
        'type': 'activated',
        'passive_stats': {
            'crit_chance': 0.01,
            'hit_chance': 0.01,
        },
        'active_stats': {
            'stat_name': 'haste',
            'stat_increment': 0.2,
            'proc_name': 'Kiss of the Spider',
            'proc_duration': 15,
            'cooldown': 120,
        },
    },
    'swarmguard': {
        'type': 'proc',
        'passive_stats': {},
        'active_stats': {
            'stat_name': 'armor_pen',
            'proc_type': 'ppm',
            'proc_rate': 10.,
        },
    },
    'maelstrom': {
        'type': 'instant_damage_proc',
        'passive_stats': {},
        'active_stats': {
            'stat_name': 'none',
            'proc_type': 'ppm',
            'proc_rate': 1.,
            'proc_name': 'Lightning Strike',
            'min_damage': 200,
            'max_damage': 300,
        },
    },
    'how': {
        'type': 'instant_damage_proc',
        'passive_stats': {},
        'active_stats': {
            'stat_name': 'none',
            'proc_type': 'ppm',
            'proc_rate': 1.,
            'proc_name': 'Flame Lash',
            'min_damage': 120*1.1, # 1.1 multiplier from Curse of Elements
            'max_damage': 180*1.1,
        },
    },
    'hoj': {
        'type': 'proc',
        'passive_stats': {
            'attack_power': 20,
        },
        'active_stats': {
            'stat_name': 'none',
            'proc_type': 'chance_on_hit',
            'proc_rate': 0.02,
        },
    },
    'sotd': {
        'type': 'passive',
        'passive_stats': {
            'attack_power': 81,
        },
    },
    'motc': {
        'type': 'passive',
        'passive_stats': {
            'attack_power': 150,
        },
    },
    'dft': {
        'type': 'passive',
        'passive_stats': {
            'attack_power': 56,
            'hit_chance': 0.02,
        },
    },
    'bb': {
        'type': 'passive',
        'passive_stats': {
            'crit_chance': 0.02,
        },
    },
    'rotgc': {
        'type': 'passive',
        'passive_stats': {
            'attack_power': 20,
            'hit_chance': 0.01,
        },
    },
    'lodestone': {
        'type': 'passive',
        'passive_stats': {
            'attack_power': 22,
        },
    },
}
