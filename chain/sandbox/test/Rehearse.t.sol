// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Rehearsal} from "../src/Rehearsal.sol";

/// The entry point chain/sandbox.py runs: parameters in through environment variables, the result
/// out as a JSON file. Not a test of anything.
contract RehearseTest is Rehearsal {
    receive() external payable {}

    function testRehearse() public {
        Params memory p = Params({
            token: vm.envAddress("SANDBOX_TOKEN"),
            router: vm.envAddress("SANDBOX_ROUTER"),
            factory: vm.envAddress("SANDBOX_FACTORY"),
            weth: vm.envAddress("SANDBOX_WETH"),
            mode: uint8(vm.envUint("SANDBOX_MODE")),
            tradeWei: vm.envUint("SANDBOX_TRADE_WEI"),
            seedWei: vm.envUint("SANDBOX_SEED_WEI"),
            gasPrice: vm.envUint("SANDBOX_GAS_PRICE"),
            venue: uint8(vm.envUint("SANDBOX_VENUE")),
            fee: uint24(vm.envUint("SANDBOX_FEE"))
        });
        Result memory r = rehearse(p);
        vm.writeFile(vm.envString("SANDBOX_OUT"), toJson(r, p.mode));
    }
}
