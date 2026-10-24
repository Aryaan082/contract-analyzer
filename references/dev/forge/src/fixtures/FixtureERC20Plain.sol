// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// A "standard ERC20": the same code as the reference, with a different name and symbol.
contract FixtureERC20Plain is ERC20 {
    constructor() ERC20("Fixture Token", "FIX") {
        _mint(msg.sender, 1_000_000e18);
    }
}
