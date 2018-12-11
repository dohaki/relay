#! /usr/bin/env python3
"""generate test data from TestCurrencyNetwork contract

The TestCurrencyNetwork contract can be deployed with:

    tl-deploy test --currency-network-contract-name=TestCurrencyNetwork


"""
import os
import sys
import json
import random
import abc
import click
from web3 import Web3
import eth_utils
import tldeploy.core


def load_contracts():
    """load the contracts.json file that is being installed with
    trustlines-contracts-bin"""
    with open(
        os.path.join(sys.prefix, "trustlines-contracts", "build", "contracts.json")
    ) as data_file:
        contracts = json.load(data_file)
        return contracts


def load_test_currency_network_abi():
    """load the TestCurrencyNetwork's ABI from the contracts.json file """
    return load_contracts()["TestCurrencyNetwork"]["abi"]


class TestDataGenerator(metaclass=abc.ABCMeta):
    def __init__(self, web3, contract):
        self.web3 = web3
        self.contract = contract

    @property
    def name(self):
        return self.__class__.__name__

    @abc.abstractmethod
    def generate_input_data(self):
        pass

    @abc.abstractmethod
    def compute_one_result(self, **kw):
        pass

    def make_test_data(self):
        result = []
        for input_data in self.generate_input_data():
            result.append(
                dict(input_data=input_data, **self.compute_one_result(**input_data))
            )
            sys.stdout.write(".")
            sys.stdout.flush()
        sys.stdout.write("\n")
        return dict(name=self.name, data=result)


class CalculateFeeGenerator(TestDataGenerator):
    def generate_input_data(self):
        def asdict(imbalance_generated, capacity_imbalance_fee_divisor):
            return dict(
                imbalance_generated=imbalance_generated,
                capacity_imbalance_fee_divisor=capacity_imbalance_fee_divisor,
            )

        prng = random.Random("666")
        for capacity_imbalance_fee_divisor in [2, 10, 50, 101, 1000]:
            yield asdict(0, capacity_imbalance_fee_divisor)
            yield asdict(10, capacity_imbalance_fee_divisor)
            yield asdict(100, capacity_imbalance_fee_divisor)
            for _ in range(40):
                imbalance_generated = prng.randint(0, 10000)
                yield asdict(imbalance_generated, capacity_imbalance_fee_divisor)

    def compute_one_result(self, imbalance_generated, capacity_imbalance_fee_divisor):
        return dict(
            calculateFees=self.contract.functions.testCalculateFees(
                imbalance_generated, capacity_imbalance_fee_divisor
            ).call(),
            calculateFeesReverse=self.contract.functions.testCalculateFeesReverse(
                imbalance_generated, capacity_imbalance_fee_divisor
            ).call(),
        )


class ImbalanceGenerated(TestDataGenerator):
    MAX_BALANCE = 2 ** 71 - 1
    MIN_BALANCE = -MAX_BALANCE

    balances = (
        MIN_BALANCE,
        MIN_BALANCE + 100,
        -1000,
        -100,
        0,
        100,
        1000,
        MAX_BALANCE - 100,
        MAX_BALANCE,
    )
    values = (0, 1, 10, 100, 1000, 2 ** 64 - 1)

    def generate_input_data(self):
        for balance in self.balances:
            for value in self.values:
                yield dict(value=value, balance=balance)

    def compute_one_result(self, value, balance):
        return dict(
            imbalance_generated=self.contract.functions.testImbalanceGenerated(
                value, balance
            ).call()
        )


class Transfer(TestDataGenerator):
    def generate_input_data(self):
        for num_hops in range(1, 6):
            addresses = [
                eth_utils.to_checksum_address(f"0x{address:040d}")
                for address in range(1, num_hops + 2)
            ]
            assert len(addresses) - 1 == num_hops
            for fees_payed_by in ["sender", "receiver"]:
                for capacity_imbalance_fee_divisor in [10, 100, 1000]:
                    for value in [1000, 10000, 1000000]:
                        yield dict(
                            fees_payed_by=fees_payed_by,
                            value=value,
                            capacity_imbalance_fee_divisor=capacity_imbalance_fee_divisor,
                            addresses=addresses,
                        )

    def compute_one_result(
        self, fees_payed_by, value, capacity_imbalance_fee_divisor, addresses
    ):
        self.contract.functions.setCapacityImbalanceFeeDivisor(
            capacity_imbalance_fee_divisor
        ).transact()

        for a, b in zip(addresses, addresses[1:]):
            self.contract.functions.setAccount(
                a, b, 100000000, 100000000, 0, 0, 0, 0, 0, 0
            ).transact()

        assert fees_payed_by in ("sender", "receiver")
        if fees_payed_by == "sender":
            self.contract.functions.testTransferSenderPays(
                addresses[0], addresses[-1], value, value, addresses[1:]
            ).transact()
        elif fees_payed_by == "receiver":
            self.contract.functions.testTransferReceiverPays(
                addresses[0], addresses[-1], value, value, addresses[1:]
            ).transact()

        balances = [
            self.contract.functions.getAccount(a, b).call()[-1]
            for a, b in zip(addresses, addresses[1:])
        ]

        return dict(balances=balances)


def generate_and_write_testdata(generator_class, web3, contract, output_directory):
    generator = generator_class(web3, contract)
    print(f"generating testdata with generator {generator.name}")
    testdata = generator.make_test_data()
    filename = os.path.join(output_directory, f"{generator.name}.json")
    print(f"writing testdata to {filename}")
    with open(filename, "w") as outfile:
        json.dump(testdata, outfile, indent=4, sort_keys=True)
        outfile.write("\n")


@click.command()
@click.option(
    "--output-directory",
    help="directory where json files are written",
    default="testdata",
)
@click.option("--address", help="address of contract", default=None)
@click.option(
    "--url", help="URL of address of contract", default="http://localhost:8545"
)
def main(address, url, output_directory):
    if not os.path.isdir(output_directory):
        click.echo(
            """Error: The output directory has not been found. Please specify it with --output-directory
or cd into the tests directory."""
        )
        sys.exit(1)
    web3 = Web3(Web3.HTTPProvider(url))
    if address is None:
        contract = tldeploy.core.deploy_network(
            web3=web3,
            name="TestNet",
            symbol="E",
            decimals=4,
            fee_divisor=100,
            currency_network_contract_name="TestCurrencyNetwork",
        )
    else:
        abi = load_test_currency_network_abi()
        contract = web3.eth.contract(abi=abi, address=address)

    for klass in (CalculateFeeGenerator, ImbalanceGenerated, Transfer):
        generate_and_write_testdata(klass, web3, contract, output_directory=output_directory)


if __name__ == "__main__":
    main()
