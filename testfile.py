# Import necessary libraries
import json
import random
import copy

# Function to load JSON data from a file
def load_item_data(filename):
    with open(filename, "r") as file:
        data = json.load(file)
    return data

# Function to randomize the price of an item
def randomize_item(item):
    curItem = copy.deepcopy(item)  # Create a deep copy of the item
    start_price = curItem["CurrentCost"]  # Get the current cost of the item
    changed_price = random.randint(1, start_price * 2 - 1)  # Generate a new price
    curItem["CurrentCost"] = changed_price  # Update the item's cost
    return curItem

# Function to randomize all items in the shop
def randomize_shop(shop_data):
    randomized_shop = []  # List to hold randomized items
    for item in shop_data:
        randomized_item = randomize_item(item)  # Randomize each item
        randomized_shop.append(randomized_item)  # Add it to the list
    return randomized_shop

# Function to create a complete new version of the game's economy
def create_fullgenome(data):
    new_genome = copy.deepcopy(data)  # Deep copy the entire game data
    new_genome["Shop"] = randomize_shop(new_genome["Shop"])  # Randomize shop prices
    return new_genome

# Main execution function
def main():
    # Load the original game data
    original_data = load_item_data("data.json")
    print("Original Data:", original_data)

    # Create a new 'genome' (a new economic state)
    new_economy = create_fullgenome(original_data)
    print("New Economy:", new_economy)

    # # Optionally, save the new economy to a file
    # with open("new_economy.json", "w") as outfile:
    #     json.dump(new_economy, outfile, indent=4)

# Check if the script is being run directly (as opposed to being imported)
if __name__ == "__main__":
    main()
