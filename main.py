# This is where the AI will make new economies
import json
import random
import copy
import math

#load the data
def load_item_data(filename):
    """
    Function: load_item_data

    Description:
    Loads item data from a JSON file and returns it as a dictionary.

    Parameters:
    - filename: The name of the JSON file to load.

    Returns:
    - data: A dictionary containing the loaded item data.
    """
    with open(filename, "r") as file:
        data = json.load(file)
    return data


#globals variables
neg_inf = float("-inf")
data = load_item_data("data.json")
genome_data = data["Genome"]
produce_data = genome_data["Produce"]
land_data = genome_data["Land_Allocation"]
production_data = genome_data["Production"]
initial_data = data["initial"]
population = initial_data["population"]
land_size = initial_data["land_amount"]
budget_data = initial_data["budget"]
needs_data = initial_data["needs"]

starting_utilities = {}
for item, details in needs_data.items():
        # Copy the utility value for each item into the starting_utilities dictionary
        starting_utilities[item] = details["utility"]



print("Produce: ", produce_data)
print("land:", land_data)
print("production:", production_data)


def randomize_land(land):
    """
    Function: randomize_land

    Description:
    Randomizes the allocation of resources across different land uses in the 'land' object.

    Parameters:
    - land: The original land object with initial allocation percentages for different land uses.

    Returns:
    - newLand: A new land object with randomized allocation percentages for different land uses.
    """
    newLand = copy.deepcopy(land)
    randomNumbers = []
    randomNumbers.append(random.uniform(0.1, 1.0))
    remaining_allocation_after_first = 1 - randomNumbers[0]
    randomNumbers.append(random.uniform(0.01, remaining_allocation_after_first))
    randomNumbers.append(1 - randomNumbers[0] - randomNumbers[1])
    land_uses = ["Grain_Crop", "Cotton_Crop", "Metal_Mining"]
    random.shuffle(land_uses)

    for i, land_use in enumerate(land_uses):
        newLand[land_use] = randomNumbers[i]
            
    return newLand


def create_genome(land): 
    """
    Function: create_genome

    Description:
    Creates a genome based on the given 'land' allocation.

    Parameters:
    - land: The original land object with initial allocation percentages for different land uses.

    Returns:
    - newGenome: A new genome object with randomized allocation percentages for different land uses.
    """

    genome = {}
    genome = copy.deepcopy(land)
    newGenome = randomize_land(genome)
    return newGenome



def basic_production(land, produce, production, size):
    """
    Function: basic_production

    Description:
    Performs basic production calculations based on land allocation, produce data, production data, and land size.

    Parameters:
    - land: The land object with allocation percentages for different land uses.
    - produce: Dictionary containing produce data.
    - production: Dictionary containing production data.
    - size: The size of the land.

    Returns:
    - produce_dict: A dictionary containing the production values for different items.
    """
    produce_dict = {}
    land_keys = ["Grain_Crop", "Cotton_Crop", "Metal_Mining"]
    produce_keys = ["Grain", "Cotton", "Metal"]
    production_keys = ["wheat", "cloth", "tool"]
    
    for item in range(len(produce_keys)):
        production_value = land[land_keys[item]] * produce[produce_keys[item]] * \
            production[production_keys[item]]["efficiency"] * size * 100
        produce_dict[production_keys[item]] = production_value

    return produce_dict


def basic_consumption(budget_data, producedGoods, marketPrice, population_size, needs_data):
    """
    Function: basic_consumption

    Description:
    Calculates basic consumption based on budget, produced goods, market prices, population size, and needs data.

    Parameters:
    - budget_data: Budget information.
    - producedGoods: Dictionary containing produced goods data.
    - marketPrice: Dictionary containing market prices.
    - population_size: Size of the population.
    - needs_data: Dictionary containing needs data.

    Returns:
    - producedGoods: Updated produced goods data after consumption.
    - consumption_dict: A dictionary containing consumption values for different items.
    """
    consumption_dict = {"wheat": 0, "cloth": 0, "tool": 0}
    
    budget_keys = ["wheat", "cloth", "tool"]
    count = 0
    utility_data = copy.deepcopy(needs_data)
    budget = budget_data
    
    while budget > 0 and count < 10:
        largest_utility = 0
        largest_product = ''
        for product in budget_keys:
            if largest_utility < utility_data[product]["utility"]:
                    largest_utility = utility_data[product]["utility"]
                    largest_product = product
        count += 1        
    
        if(consumption_dict[largest_product] * 100 <= producedGoods[largest_product] and budget - marketPrice[largest_product]['price'] >=0):
            consumption_dict[largest_product] += 1
            budget -= marketPrice[largest_product]['price'] 
            count = 0
        
        for product in utility_data:
            if product == largest_product:
                utility_data[product]["utility"] -= utility_data[product]["downWeighting"]
            else:
                utility_data[product]["utility"] += utility_data[product]["upWeighting"]
    for item in consumption_dict:
        consumption_dict[item] *= population_size
        producedGoods[item] -= consumption_dict[item]
                  
    return producedGoods, consumption_dict



def adjust_price_based_on_supply_demand(production_data, produce_dict):
    """
    Adjusts the prices of products based on their supply and demand.

    Parameters:
    - production_data: A dictionary containing information about the demand and price of each product.
    - produce_dict: A dictionary containing the supply of each product.

    Returns:
    - Updated production data with adjusted prices.
    """
    updated_production_data = copy.deepcopy(production_data)

    # Iterate through the products and their supply in the produce_dict
    for product, supply in produce_dict.items():
        # Retrieve the demand and details from the production data
        if product in updated_production_data:
            details = updated_production_data[product]
            demand = details['demand']
            # Simple price adjustment logic
            if supply > demand:  # adjust to use % instead of flat number.
                # Supply exceeds demand, reduce price to encourage buying
                price_adjustment_factor = 0.9  # reduce price by 10%
            else:
                # Demand exceeds supply, increase price due to scarcity
                price_adjustment_factor = 1.1  # increase price by 10%

            # Adjust the price based on the factor
            updated_price = details['price'] * price_adjustment_factor
            # Update the product's price in the production data
            updated_production_data[product]['price'] = updated_price
    return updated_production_data

def breed_parents(genome1, genome2):
    """
    Creates a new genome (child) by combining traits from two parent genomes.

    Parameters:
    - genome1: The first parent genome.
    - genome2: The second parent genome.

    Returns:
    - A new child genome.
    """
    parent1 = copy.deepcopy(genome1)
    parent2 = copy.deepcopy(genome2)

    child = copy.deepcopy(genome1)
    for key in parent1:
        if key == 'Grain_Crop':
            child[key] = parent1[key]
        else:
            child[key] = parent2[key]
    child = normalize(child)
    child = mutation(child)
    return child


def mutation(genome):
    """
    Randomly mutates a genome's land allocation.

    Parameters:
    - genome: The genome to be mutated.

    Returns:
    - The mutated genome.
    """
    #get a high or low randomly
    #based on this, a high would add percentage to a random land allocation
    #redefine the other two land allocations to be less than 1
    randomMutate = random.randint(0, 100)
    if randomMutate >= 50:
        land_uses = ["Grain_Crop", "Cotton_Crop", "Metal_Mining"]
        
        # Shuffle the list to randomize which land use gets which number
        random.shuffle(land_uses)

        for i in range(len(land_uses)):
            amount = random.uniform(-genome[land_uses[i]], genome[land_uses[i]])
            genome[land_uses[i]] += amount
        
        genome = normalize(genome)

    return genome

    
def normalize(genome):
    """
    Executes the basic simulation for a given genome.

    Parameters:
    - genome: The genome to simulate.

    Returns:
    - The remaining goods after consumption and the consumption data.
    """
    land_sum = sum(genome.values())
    
    if land_sum > 1:
        # Normalize the land allocations using Min-Max scaling
        for key in genome:
            # Normalize each value in the genome
            genome[key] = genome[key] / land_sum

    return genome

def consumption_surplus(num_consumed, need, positive_weight, negative_weight):
    surplus = num_consumed - need
    if surplus >= 0:
        return positive_weight * surplus
    else:
        return negative_weight * surplus
 
def fitness(con_genome, consume_dict):
    """
    Calculates the fitness score for a genome based on its consumption and production.

    Parameters:
    - con_genome: The genome's contribution towards production.
    - consume_dict: The genome's contribution towards consumption.

    Returns:
    - The fitness score.
    """
    k = 0.6 # higher = production weight, lower = consumption weighted
    cs_pos_weight = 0.5
    cs_neg_weight = 0.1
    ps_pos_weight = 0.2
    ps_neg_weight = 0.01
    
    total_fitness = 0
    for key in con_genome:
    
        if con_genome[key] >= 0:
            new_weight = ps_pos_weight
        else:
            new_weight = ps_neg_weight
        fitness = k * (con_genome[key] + consume_dict[key]) + con_genome[key] * new_weight \
            + (1-k) * (con_genome[key] + consume_dict[key]) + \
                          consumption_surplus(consumption_dict[key], needs_data[key]["need"], cs_pos_weight, cs_neg_weight)
        total_fitness += fitness
   
        


    return total_fitness

def run_function(genome):
    """
    Executes the basic simulation for a given genome.

    Parameters:
    - genome: The genome to simulate.

    Returns:
    - The remaining goods after consumption and the consumption data.
    """
    producedGoods = basic_production(genome, produce_data, production_data, land_size)

    newPrices = adjust_price_based_on_supply_demand(production_data, producedGoods)
    
    consumption, consumption_dict = basic_consumption(budget_data, producedGoods, newPrices, population, needs_data)
    
    # use this data to compare to the fitness
    return consumption, consumption_dict
###################################################################################################
if __name__ == "__main__":
    
    genomes = {}
    numOfParents = 80
    generations = 20
    
    for i in range(numOfParents):
        genome = create_genome(land_data)
        genome = mutation(genome)
        genome_consumption, consumption_dict = run_function(genome)
        score = fitness(genome_consumption, consumption_dict)
        genomes[i] = ([genome, score, genome_consumption, consumption_dict])
        
    #out of the parents, sort them and chose the best 2. Once we have lots of parents, sort in groups
    sorted_genomes = sorted(genomes.items(), key=lambda item: item[1][1], reverse=True)
    
    for i in range(generations):
        sorted_genomes = sorted(genomes.items(), key=lambda item: item[1][1], reverse=True)
        for j in range(0, len(sorted_genomes), 2):   
            child = breed_parents(sorted_genomes[j][1][0],sorted_genomes[j+1][1][0])
            child_genome, consumption_dict = run_function(child)
            
            child_score = fitness(child_genome, consumption_dict)
            
            minScore = min(genomes[j+1][1],child_score)
            if(minScore == child_score):
                continue
            else:
                genomes[j+1] = ([child, child_score, genome_consumption, consumption_dict])

    final_genomes = sorted(genomes.items(), key=lambda item: item[1][1], reverse=True)

    print()
    print("Best Genome land allocation:", final_genomes[0][1][0])
    print("It's score: ", final_genomes[0][1][1])
    print("It's surplus: ", final_genomes[0][1][2])
    print("It's consumption: ", final_genomes[0][1][3])
