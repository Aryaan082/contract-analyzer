// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";

/// Test data: the usual ERC20 + Ownable + Pausable + owner-only mint combination. Most of its
/// extra functions are standard modules; only `mint` is not.
contract FixtureERC20Wizard is ERC20, Ownable, Pausable {
    constructor() ERC20("Fixture Token", "FIX") Ownable(msg.sender) {}

    function pause() external onlyOwner {
        _pause();
    }

    function unpause() external onlyOwner {
        _unpause();
    }

    function mint(address to, uint256 value) external onlyOwner {
        _mint(to, value);
    }

    function _update(address from, address to, uint256 value) internal override whenNotPaused {
        super._update(from, to, value);
    }
}
